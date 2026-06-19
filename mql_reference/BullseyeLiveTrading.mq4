//+------------------------------------------------------------------+
//|                                            BullseyeLiveTrading.mq4 |
//|                                  Enhanced socket client for live trading |
//+------------------------------------------------------------------+
#property strict
#include <Sockets.mqh>
#include <SimpleJson.mqh>

//--- Input parameters
input string   Hostname = "localhost";           // Server hostname
input ushort   ServerPort = 7000;               // Server port
input string   TradingInstrument = "";          // Instrument (empty = chart symbol)
input string   Timeframe = "H4";                // Timeframe for bar data
input int      HeartbeatInterval = 30;          // Heartbeat interval (seconds)
input bool     EnableTrading = true;            // Enable live trading
input double   DefaultLotSize = 0.1;            // Default lot size
input int      Slippage = 3;                    // Slippage in points

//--- Global variables
ClientSocket* glbClientSocket = NULL;
datetime lastBarTime = 0;
datetime lastHeartbeat = 0;
datetime lastAccountInfo = 0;  // Track last account info send
int accountInfoInterval = 15;  // Default interval (seconds), updated from config
bool isConnected = false;
string activeInstrument = "";

//--- Data structures for tracking positions
struct OpenPosition {
   int ticket;
   string strategy_id;
   datetime openTime;
   double requested_price;  // Track requested price for slippage calculation
   int exit_after_bars;     // Number of bars before automatic exit (0 = disabled)
   datetime entry_bar_time; // Bar time when position was opened
   string instrument;       // Instrument symbol for bar tracking
   string timeframe;        // Timeframe for bar tracking
   int magic;                // Magic number for this specific position
};

OpenPosition openPositions[];
int positionCount = 0;

//--- Closed P/L tracking per strategy
struct StrategyPL {
   string strategy_id;
   double closed_pl;
};

StrategyPL closedPLTracking[];
int plTrackingCount = 0;

//--- Configuration received from Python
struct InstrumentConfig {
   string symbol;
   string timeframe;
   datetime lastBarTime;
   long chartId;  // Chart ID if opened dynamically
};

InstrumentConfig configuredInstruments[];
int configCount = 0;
bool configReceived = false;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit() {
   Print("BullseyeLiveTrading EA initialized");
   
   // Initialize position tracking
   ArrayResize(openPositions, 100);
   positionCount = 0;
   
   // Initialize closed P/L tracking
   ArrayResize(closedPLTracking, 200);
   plTrackingCount = 0;
   
   // Set timer for connection handling
   EventSetTimer(5);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   
   if (glbClientSocket) {
      Print("Closing socket connection");
      delete glbClientSocket;
      glbClientSocket = NULL;
   }
   
   Print("BullseyeLiveTrading EA deinitialized");
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick() {
   // Check for new bar and send market data
   if (isConnected && configReceived) {
      CheckAndSendNewBars();
   }
}

//+------------------------------------------------------------------+
//| Timer function                                                     |
//+------------------------------------------------------------------+
void OnTimer() {
   HandleConnection();
   
   if (isConnected) {
      SendHeartbeatIfNeeded();
      SendAccountInfoIfNeeded();
      CheckClosedPositions();
      CheckBarBasedExits();
   }
}

//+------------------------------------------------------------------+
//| Check for new bars on all configured instruments                  |
//+------------------------------------------------------------------+
void CheckAndSendNewBars() {
   // Send bars for all configured instruments
   for (int i = 0; i < configCount; i++) {
      string symbol = configuredInstruments[i].symbol;
      string tf = configuredInstruments[i].timeframe;
      int period = StringToTimeframe(tf);
      
      datetime currentBarTime = iTime(symbol, period, 0);
      
      // Check if this is a new bar for this instrument
      if (currentBarTime != configuredInstruments[i].lastBarTime && currentBarTime > 0) {
         configuredInstruments[i].lastBarTime = currentBarTime;
         SendMarketData(symbol, tf, period, currentBarTime);
      }
   }
}

//+------------------------------------------------------------------+
//| Send MARKET_DATA message for specific instrument                  |
//+------------------------------------------------------------------+
void SendMarketData(string symbol, string timeframe, int period, datetime barTime) {
   double openPrice = iOpen(symbol, period, 0);
   double highPrice = iHigh(symbol, period, 0);
   double lowPrice = iLow(symbol, period, 0);
   double closePrice = iClose(symbol, period, 0);
   long volume = (long)iVolume(symbol, period, 0);
   
   // Format timestamp as ISO 8601
   string timestamp = TimeToString(barTime, TIME_DATE|TIME_SECONDS);
   StringReplace(timestamp, ".", "-");
   StringReplace(timestamp, " ", "T");
   
   // Create MARKET_DATA message
   string jsonMsg = CreateSimpleJson(
      "type", "MARKET_DATA",
      "instrument", symbol,
      "timeframe", timeframe,
      "timestamp", timestamp,
      "open", DoubleToString(openPrice, Digits),
      "high", DoubleToString(highPrice, Digits),
      "low", DoubleToString(lowPrice, Digits),
      "close", DoubleToString(closePrice, Digits),
      "volume", IntegerToString(volume)
   );
   
   jsonMsg += "\r\n";
   
   if (glbClientSocket.Send(jsonMsg)) {
      Print("Sent MARKET_DATA: ", symbol, " ", timeframe, " at ", timestamp);
   } else {
      Print("Failed to send MARKET_DATA for ", symbol);
   }
}

//+------------------------------------------------------------------+
//| DEPRECATED: Old CheckAndSendNewBar function                       |
//+------------------------------------------------------------------+
void CheckAndSendNewBar() {
   datetime currentBarTime = iTime(activeInstrument, GetTimeframePeriod(), 0);
   
   // Check if new bar has formed
   if (currentBarTime != lastBarTime && lastBarTime != 0) {
      // Send the completed bar (index 1, not current bar 0)
      SendMarketData(1);
   }
   
   lastBarTime = currentBarTime;
}

//+------------------------------------------------------------------+
//| Task 3.7: Send MARKET_DATA message to Python                      |
//+------------------------------------------------------------------+
void SendMarketData(int barIndex) {
   if (!glbClientSocket || !glbClientSocket.IsSocketConnected()) return;
   
   int tf = GetTimeframePeriod();
   
   // Get OHLCV data
   datetime barTime = iTime(activeInstrument, tf, barIndex);
   double openPrice = iOpen(activeInstrument, tf, barIndex);
   double highPrice = iHigh(activeInstrument, tf, barIndex);
   double lowPrice = iLow(activeInstrument, tf, barIndex);
   double closePrice = iClose(activeInstrument, tf, barIndex);
   long volume = iVolume(activeInstrument, tf, barIndex);
   
   // Format timestamp as ISO 8601
   string timestamp = TimeToString(barTime, TIME_DATE|TIME_SECONDS);
   StringReplace(timestamp, ".", "-");
   StringReplace(timestamp, " ", "T");
   
   // Create MARKET_DATA message
   string jsonMsg = CreateSimpleJson(
      "type", "MARKET_DATA",
      "instrument", activeInstrument,
      "timeframe", Timeframe,
      "timestamp", timestamp,
      "open", DoubleToString(openPrice, Digits),
      "high", DoubleToString(highPrice, Digits),
      "low", DoubleToString(lowPrice, Digits),
      "close", DoubleToString(closePrice, Digits),
      "volume", IntegerToString(volume)
   );
   
   jsonMsg += "\r\n";
   
   if (glbClientSocket.Send(jsonMsg)) {
      Print("Sent MARKET_DATA: ", activeInstrument, " ", Timeframe, " at ", timestamp);
   } else {
      Print("Failed to send MARKET_DATA");
   }
}

//+------------------------------------------------------------------+
//| Send heartbeat message                                            |
//+------------------------------------------------------------------+
void SendHeartbeatIfNeeded() {
   if (TimeCurrent() - lastHeartbeat < HeartbeatInterval) return;
   
   if (!glbClientSocket || !glbClientSocket.IsSocketConnected()) return;
   
   string timestamp = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
   StringReplace(timestamp, ".", "-");
   StringReplace(timestamp, " ", "T");
   
   string jsonMsg = CreateSimpleJson(
      "type", "HEARTBEAT",
      "timestamp", timestamp
   );
   
   jsonMsg += "\r\n";
   
   if (glbClientSocket.Send(jsonMsg)) {
      lastHeartbeat = TimeCurrent();
   }
}

//+------------------------------------------------------------------+
//| Handle socket connection                                           |
//+------------------------------------------------------------------+
void HandleConnection() {
   // Create socket if doesn't exist
   if (!glbClientSocket) {
      glbClientSocket = new ClientSocket(Hostname, ServerPort);
      
      if (glbClientSocket.IsSocketConnected()) {
         Print("Connected to Python server at ", Hostname, ":", ServerPort);
         isConnected = true;
         lastHeartbeat = TimeCurrent();
         
         // Send initial connection message
         SendConnectionMessage();
      } else {
         Print("Failed to connect to server");
         isConnected = false;
      }
   }
   
   // Process incoming messages
   if (glbClientSocket && glbClientSocket.IsSocketConnected()) {
      string receivedMsg;
      do {
         receivedMsg = glbClientSocket.Receive("\r\n");
         if (receivedMsg != "") {
            ProcessMessage(receivedMsg);
         }
      } while (receivedMsg != "");
   } else {
      // Connection lost
      if (isConnected) {
         Print("Connection lost. Will retry.");
         isConnected = false;
      }
      
      if (glbClientSocket) {
         delete glbClientSocket;
         glbClientSocket = NULL;
      }
   }
}

//+------------------------------------------------------------------+
//| Send initial connection message                                    |
//+------------------------------------------------------------------+
void SendConnectionMessage() {
   string jsonMsg = CreateSimpleJson(
      "type", "CONNECTION",
      "account", IntegerToString(AccountNumber()),
      "broker", AccountCompany(),
      "platform", "MT4"
   );
   
   jsonMsg += "\r\n";
   glbClientSocket.Send(jsonMsg);
   Print("Connection message sent - awaiting configuration from Python...");
}

//+------------------------------------------------------------------+
//| Send account info if needed (every configurable interval)         |
//+------------------------------------------------------------------+
void SendAccountInfoIfNeeded() {
   datetime currentTime = TimeCurrent();
   
   // Send account info based on configured interval
   if (currentTime - lastAccountInfo >= accountInfoInterval) {
      lastAccountInfo = currentTime;
      
      double balance = AccountBalance();
      double equity = AccountEquity();
      double freeMargin = AccountFreeMargin();
      double margin = AccountMargin();
      double marginLevel = (margin > 0) ? (equity / margin) * 100.0 : 0.0;
      
      string timestamp = TimeToString(currentTime, TIME_DATE|TIME_SECONDS);
      StringReplace(timestamp, ".", "-");
      StringReplace(timestamp, " ", "T");
      
      string jsonMsg = CreateSimpleJson(
         "type", "ACCOUNT_INFO",
         "balance", DoubleToString(balance, 2),
         "equity", DoubleToString(equity, 2),
         "free_margin", DoubleToString(freeMargin, 2),
         "margin", DoubleToString(margin, 2),
         "margin_level", DoubleToString(marginLevel, 2),
         "timestamp", timestamp
      );
      
      jsonMsg += "\r\n";
      glbClientSocket.Send(jsonMsg);
      
      // Send position info immediately after account info
      SendPositionInfo();
   }
}

//+------------------------------------------------------------------+
//| Build and send position info for all strategies                   |
//+------------------------------------------------------------------+
void SendPositionInfo() {
   if (!glbClientSocket || !glbClientSocket.IsSocketConnected()) return;
   
   // Build position data string: "strategy_id:position:open_pl:closed_pl;..."
   string positionsStr = "";
   
   // Create a map to aggregate position data by strategy
   string strategyIds[];
   int strategyPosition[];
   double strategyOpenPL[];
   double strategyClosedPL[];
   int uniqueStrategies = 0;
   
   // Process open positions
   for (int i = 0; i < positionCount; i++) {
      int ticket = openPositions[i].ticket;
      string sid = openPositions[i].strategy_id;
      
      if (!OrderSelect(ticket, SELECT_BY_TICKET)) continue;
      
      // Find or create strategy entry
      int idx = -1;
      for (int j = 0; j < uniqueStrategies; j++) {
         if (strategyIds[j] == sid) {
            idx = j;
            break;
         }
      }
      
      if (idx < 0) {
         // New strategy
         idx = uniqueStrategies;
         ArrayResize(strategyIds, uniqueStrategies + 1);
         ArrayResize(strategyPosition, uniqueStrategies + 1);
         ArrayResize(strategyOpenPL, uniqueStrategies + 1);
         ArrayResize(strategyClosedPL, uniqueStrategies + 1);
         
         strategyIds[idx] = sid;
         strategyPosition[idx] = 0;
         strategyOpenPL[idx] = 0.0;
         strategyClosedPL[idx] = 0.0;
         uniqueStrategies++;
      }
      
      // Update position count and P/L
      double lots = OrderLots();
      if (OrderType() == OP_BUY) {
         strategyPosition[idx] += (int)(lots * 100);  // Convert to contracts (assuming 1 lot = 100 contracts)
      } else if (OrderType() == OP_SELL) {
         strategyPosition[idx] -= (int)(lots * 100);  // Negative for short
      }
      
      strategyOpenPL[idx] += OrderProfit() + OrderSwap() + OrderCommission();
   }
   
   // Add closed P/L from tracking
   for (int i = 0; i < plTrackingCount; i++) {
      string sid = closedPLTracking[i].strategy_id;
      double closedPL = closedPLTracking[i].closed_pl;
      
      // Find strategy in our aggregated list
      int idx = -1;
      for (int j = 0; j < uniqueStrategies; j++) {
         if (strategyIds[j] == sid) {
            idx = j;
            break;
         }
      }
      
      if (idx < 0) {
         // New strategy (no open position, but has closed trades)
         idx = uniqueStrategies;
         ArrayResize(strategyIds, uniqueStrategies + 1);
         ArrayResize(strategyPosition, uniqueStrategies + 1);
         ArrayResize(strategyOpenPL, uniqueStrategies + 1);
         ArrayResize(strategyClosedPL, uniqueStrategies + 1);
         
         strategyIds[idx] = sid;
         strategyPosition[idx] = 0;
         strategyOpenPL[idx] = 0.0;
         strategyClosedPL[idx] = closedPL;
         uniqueStrategies++;
      } else {
         strategyClosedPL[idx] = closedPL;
      }
   }
   
   // Build positions string
   for (int i = 0; i < uniqueStrategies; i++) {
      if (i > 0) positionsStr += ";";
      positionsStr += strategyIds[i] + "|" + 
                      IntegerToString(strategyPosition[i]) + "|" + 
                      DoubleToString(strategyOpenPL[i], 2) + "|" + 
                      DoubleToString(strategyClosedPL[i], 2);
   }
   
   // Send POSITION_INFO message
   string jsonMsg = CreateSimpleJson(
      "type", "POSITION_INFO",
      "positions", positionsStr
   );
   
   jsonMsg += "\r\n";
   glbClientSocket.Send(jsonMsg);
}

//+------------------------------------------------------------------+
//| Check for positions that should be closed based on bar count      |
//+------------------------------------------------------------------+
void CheckBarBasedExits() {
   // Check each position for bar-based exits
   for (int i = positionCount - 1; i >= 0; i--) {
      // Skip if exit_after_bars is not set
      if (openPositions[i].exit_after_bars <= 0) continue;
      
      int ticket = openPositions[i].ticket;
      string instrument = openPositions[i].instrument;
      string tf = openPositions[i].timeframe;
      datetime entry_bar_time = openPositions[i].entry_bar_time;
      int exit_after_bars = openPositions[i].exit_after_bars;
      string strategy_id = openPositions[i].strategy_id;
      
      // Get current bar time
      int period = StringToTimeframe(tf);
      datetime current_bar_time = iTime(instrument, period, 0);
      
      // Count bars elapsed since entry
      int bars_elapsed = 0;
      for (int bar = 0; bar < 1000; bar++) {  // Limit search
         datetime bar_time = iTime(instrument, period, bar);
         if (bar_time == 0) break;  // No more bars
         if (bar_time <= entry_bar_time) {
            bars_elapsed = bar;
            break;
         }
      }
      
      // Check if we should close
      if (bars_elapsed >= exit_after_bars) {
         Print("Auto-closing position ticket #", ticket, " for ", strategy_id, 
               " - ", bars_elapsed, " bars elapsed (exit_after_bars=", exit_after_bars, ")");
         
         // Verify order still exists and is open
         if (OrderSelect(ticket, SELECT_BY_TICKET)) {
            if (OrderCloseTime() == 0) {  // Order is still open
               // Get close price
               string orderInstrument = OrderSymbol();
               int orderDigits = (int)MarketInfo(orderInstrument, MODE_DIGITS);
               
               double closePrice;
               if (OrderType() == OP_BUY) {
                  closePrice = MarketInfo(orderInstrument, MODE_BID);
               } else {
                  closePrice = MarketInfo(orderInstrument, MODE_ASK);
               }
               
               closePrice = NormalizeDouble(closePrice, orderDigits);
               
               // Close the order
               bool closed = OrderClose(ticket, OrderLots(), closePrice, Slippage, clrGray);
               
               if (closed) {
                  Print("Position closed by bar count - ticket #", ticket);
                  
                  // Calculate profit/loss
                  double grossProfit = OrderProfit();
                  double commission = OrderCommission();
                  double swap = OrderSwap();
                  double netProfit = grossProfit + commission + swap;
                  
                  // Update closed P/L tracking
                  UpdateClosedPL(strategy_id, netProfit);
                  
                  // Send TRADE_UPDATE
                  SendTradeUpdate("CLOSE", ticket, strategy_id, closePrice, grossProfit, netProfit);
                  
                  // Remove from tracking
                  if (i < positionCount - 1) {
                     for (int k = i; k < positionCount - 1; k++) {
                        openPositions[k] = openPositions[k + 1];
                     }
                  }
                  positionCount--;
               } else {
                  Print("Failed to close position by bar count - ticket #", ticket, " Error: ", GetLastError());
               }
            } else {
               Print("Position already closed - ticket #", ticket);
               // Remove from tracking
               if (i < positionCount - 1) {
                  for (int k = i; k < positionCount - 1; k++) {
                     openPositions[k] = openPositions[k + 1];
                  }
               }
               positionCount--;
            }
         } else {
            Print("Position no longer exists - ticket #", ticket);
            // Remove from tracking
            if (i < positionCount - 1) {
               for (int k = i; k < positionCount - 1; k++) {
                  openPositions[k] = openPositions[k + 1];
               }
            }
            positionCount--;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check for positions that were closed outside EA control           |
//+------------------------------------------------------------------+
void CheckClosedPositions() {
   // Check each position in our tracking array
   for (int i = positionCount - 1; i >= 0; i--) {
      int ticket = openPositions[i].ticket;
      string strategy_id = openPositions[i].strategy_id;
      
      // Try to select the order/position by ticket
      bool positionExists = false;
      
      // MT4: Check if order still exists and is open
      if (OrderSelect(ticket, SELECT_BY_TICKET)) {
         // Order exists, check if it's still open
         if (OrderCloseTime() == 0) {
            positionExists = true;
         }
      }
      
      // If position no longer exists or is closed, handle it
      if (!positionExists) {
         Print("Position closed externally detected: Ticket #", ticket, " Strategy: ", strategy_id);
         
         // Try to get close info
         double closePrice = 0.0;
         double profit = 0.0;
         double commission = 0.0;
         double swap = 0.0;
         double netProfit = 0.0;
         
         if (OrderSelect(ticket, SELECT_BY_TICKET)) {
            closePrice = OrderClosePrice();
            profit = OrderProfit();
            commission = OrderCommission();
            swap = OrderSwap();
            netProfit = profit + commission + swap;
            
            Print("Close details: Price=", closePrice, " Profit=", profit, 
                  " Commission=", commission, " Swap=", swap, " Net=", netProfit);
         } else {
            Print("Warning: Could not retrieve close details for ticket #", ticket);
         }
         
         // Update closed P/L tracking
         UpdateClosedPL(strategy_id, netProfit);
         
         // Send TRADE_UPDATE (CLOSE) to Python
         SendTradeUpdate("CLOSE", ticket, strategy_id, closePrice, profit, netProfit);
         
         // Remove from our tracking array
         if (i < positionCount - 1) {
            // Shift array down
            for (int k = i; k < positionCount - 1; k++) {
               openPositions[k] = openPositions[k + 1];
            }
         }
         positionCount--;
      }
   }
}

//+------------------------------------------------------------------+
//| Update closed P/L for a strategy                                  |
//+------------------------------------------------------------------+
void UpdateClosedPL(string strategy_id, double net_profit) {
   // Find existing entry
   int idx = -1;
   for (int i = 0; i < plTrackingCount; i++) {
      if (closedPLTracking[i].strategy_id == strategy_id) {
         idx = i;
         break;
      }
   }
   
   if (idx < 0) {
      // Create new entry
      if (plTrackingCount >= ArraySize(closedPLTracking)) {
         ArrayResize(closedPLTracking, plTrackingCount + 50);
      }
      idx = plTrackingCount;
      closedPLTracking[idx].strategy_id = strategy_id;
      closedPLTracking[idx].closed_pl = net_profit;
      plTrackingCount++;
   } else {
      // Update existing
      closedPLTracking[idx].closed_pl += net_profit;
   }
}

//+------------------------------------------------------------------+
//| Task 3.8 & 3.9: Process incoming messages                         |
//+------------------------------------------------------------------+
void ProcessMessage(string message) {
   // Debug: Print raw message
   //Print("RAW MESSAGE: [", message, "]");
   //Print("RAW MESSAGE LENGTH: ", StringLen(message));
   
   JsonKeyValue jsonPairs[];
   
   if (!ParseSimpleJson(message, jsonPairs)) {
      Print("Failed to parse JSON: ", message);
      return;
   }
   
   // Debug: Print all parsed pairs
   //Print("Parsed ", ArraySize(jsonPairs), " key-value pairs");
   //for (int i = 0; i < ArraySize(jsonPairs); i++) {
   //   Print("  Pair ", i, ": key='", jsonPairs[i].key, "' value='", jsonPairs[i].value, "'");
   //}
   
   string msgType = GetJsonValue(jsonPairs, "type");
   //Print("EXTRACTED TYPE: [", msgType, "] length=", StringLen(msgType));
   
   if (msgType == "CONFIG") {
      // Handle CONFIG message - received from Python with instruments/timeframes
      HandleConfigMessage(jsonPairs);
   }
   else if (msgType == "ORDER") {
      // Task 3.8: Handle ORDER message
      HandleOrderMessage(jsonPairs);
   }
   else if (msgType == "HEARTBEAT") {
      // Acknowledge heartbeat from server
      // Print("Recived heartbeat");
   }
   else if (msgType == "ERROR") {
      string errorMsg = GetJsonValue(jsonPairs, "message");
      Print("Error from server: ", errorMsg);
   }
   else if (msgType == "ACK") {
      string ackFor = GetJsonValue(jsonPairs, "ack_for");
      Print("ACK received for: ", ackFor);
   }
   else if (msgType == "CONNECTION") {
      // Connection acknowledgment
   }
   else {
      Print("Unknown message type: ", msgType);
   }
}

//+------------------------------------------------------------------+
//| Task 3.8: Handle ORDER message from Python                        |
//+------------------------------------------------------------------+
void HandleOrderMessage(JsonKeyValue &jsonPairs[]) {
   if (!EnableTrading) {
      Print("Trading disabled - ORDER ignored");
      return;
   }
   
   string strategy_id = GetJsonValue(jsonPairs, "strategy_id");
   string instrument = GetJsonValue(jsonPairs, "instrument");
   string side = GetJsonValue(jsonPairs, "side");
   string action = GetJsonValue(jsonPairs, "action");
   double price = StringToDouble(GetJsonValue(jsonPairs, "price"));
   double stopLoss = StringToDouble(GetJsonValue(jsonPairs, "stop_loss"));
   double takeProfit = StringToDouble(GetJsonValue(jsonPairs, "take_profit"));
   double lotSize = StringToDouble(GetJsonValue(jsonPairs, "size"));
   int exitAfterBars = (int)StringToInteger(GetJsonValue(jsonPairs, "exit_after_bars"));
   int magic = (int)StringToInteger(GetJsonValue(jsonPairs, "magic"));
   
   Print("Received ORDER: ", action, " ", side, " ", instrument, " Strategy: ", strategy_id, " ExitAfterBars: ", exitAfterBars);
   
   // Validate symbol exists and is tradeable (for OPEN orders)
   if (action == "OPEN") {
      // Check if symbol is available in Market Watch
      if (MarketInfo(instrument, MODE_BID) == 0 && GetLastError() == ERR_UNKNOWN_SYMBOL) {
         Print("Symbol not available: ", instrument);
         SendTradeError(strategy_id, 4000);  // Custom error: Symbol not available
         return;
      }
      
      ExecuteOpenOrder(strategy_id, instrument, side, lotSize, stopLoss, takeProfit, price, exitAfterBars, magic);
   }
   else if (action == "CLOSE") {
      ExecuteCloseOrder(strategy_id);
   }
   else {
      Print("Unknown order action: ", action);
   }
}

//+------------------------------------------------------------------+
//| Execute OPEN order                                                |
//+------------------------------------------------------------------+
void ExecuteOpenOrder(string strategy_id, string instrument, string side, double lots, double sl, double tp, double requested_price = 0, int exit_after_bars = 0, int magic = 0) {
   int orderType;
   double openPrice;
   color arrowColor;
   int symbolDigits = (int)MarketInfo(instrument, MODE_DIGITS);
   
   // Determine order type and price
   if (side == "long") {
      orderType = OP_BUY;
      openPrice = MarketInfo(instrument, MODE_ASK);
      arrowColor = clrBlue;
   } else {
      orderType = OP_SELL;
      openPrice = MarketInfo(instrument, MODE_BID);
      arrowColor = clrRed;
   }
   
   // Validate price is available
   if (openPrice <= 0) {
      Print("Invalid price for ", instrument, ": ", openPrice);
      SendTradeError(strategy_id, 134);  // ERR_NOT_ENOUGH_MONEY or market closed
      return;
   }
   
   // Store requested price if not provided (use current market price)
   if (requested_price <= 0) {
      requested_price = openPrice;
   }
   
   // Use default lot size if not specified
   if (lots <= 0) lots = DefaultLotSize;
   
   // Validate lot size against symbol limits
   double minLot = MarketInfo(instrument, MODE_MINLOT);
   double maxLot = MarketInfo(instrument, MODE_MAXLOT);
   double lotStep = MarketInfo(instrument, MODE_LOTSTEP);
   
   if (lots < minLot) {
      Print("Lot size ", lots, " below minimum ", minLot, " for ", instrument);
      SendTradeError(strategy_id, 131);  // ERR_INVALID_TRADE_VOLUME
      return;
   }
   
   if (lots > maxLot) {
      Print("Lot size ", lots, " above maximum ", maxLot, " for ", instrument);
      SendTradeError(strategy_id, 131);  // ERR_INVALID_TRADE_VOLUME
      return;
   }
   
   // Normalize lot size to lot step
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = NormalizeDouble(lots, 2);
   
   // Normalize prices using the symbol's digits
   openPrice = NormalizeDouble(openPrice, symbolDigits);
   if (sl > 0) sl = NormalizeDouble(sl, symbolDigits);
   if (tp > 0) tp = NormalizeDouble(tp, symbolDigits);
   
   // Shorten strategy_id for MT4's comment limit (31 characters)
   string orderComment = strategy_id;
   
   // Look for "_long" or "_short" and replace with "_l" or "_s"
   int longPos = StringFind(orderComment, "_long");
   int shortPos = StringFind(orderComment, "_short");
   
   if (longPos >= 0) {
      // Found "_long" - keep everything before it and add "_l"
      orderComment = StringSubstr(orderComment, 0, longPos) + "_l";
   }
   else if (shortPos >= 0) {
      // Found "_short" - keep everything before it and add "_s"
      orderComment = StringSubstr(orderComment, 0, shortPos) + "_s";
   }
   
   // If still too long, truncate to 31 characters
   if (StringLen(orderComment) > 31) {
      orderComment = StringSubstr(orderComment, 0, 31);
      Print("Warning: Strategy ID truncated to 31 chars: ", orderComment);
   }
   
   Print("Opening order with comment: [", orderComment, "] (length: ", StringLen(orderComment), ")");
   
   // Open order on the specified instrument
   int ticket = OrderSend(
      instrument,
      orderType,
      lots,
      openPrice,
      Slippage,
      sl,
      tp,
      orderComment,
      magic,
      0,
      arrowColor
   );
   
   if (ticket > 0) {
      Print("Order opened successfully: Ticket #", ticket);
      
      // Find instrument config to get timeframe
      string tf = Timeframe;  // Default
      for (int i = 0; i < configCount; i++) {
         if (configuredInstruments[i].symbol == instrument) {
            tf = configuredInstruments[i].timeframe;
            break;
         }
      }
      
      // Get current bar time for the instrument/timeframe
      int period = StringToTimeframe(tf);
      datetime entry_bar_time = iTime(instrument, period, 0);
      
      // Track position with all parameters
      if (positionCount < ArraySize(openPositions)) {
         openPositions[positionCount].ticket = ticket;
         openPositions[positionCount].strategy_id = strategy_id;
         openPositions[positionCount].openTime = TimeCurrent();
         openPositions[positionCount].requested_price = requested_price;
         openPositions[positionCount].exit_after_bars = exit_after_bars;
         openPositions[positionCount].entry_bar_time = entry_bar_time;
         openPositions[positionCount].instrument = instrument;
         openPositions[positionCount].timeframe = tf;
         openPositions[positionCount].magic = magic;
         positionCount++;
         
         if (exit_after_bars > 0) {
            Print("Position will auto-close after ", exit_after_bars, " bars on ", instrument, " ", tf);
         }
      }
      
      // Task 3.9: Send TRADE_UPDATE (FILL) with requested price for slippage calculation
      if (OrderSelect(ticket, SELECT_BY_TICKET)) {
         SendTradeUpdate("FILL", ticket, strategy_id, OrderOpenPrice(), 0, 0, requested_price);
      }
   } else {
      int error = GetLastError();
      Print("Order failed: Error ", error, " - ", ErrorDescription(error));
      
      // Send error notification
      SendTradeError(strategy_id, error);
   }
}

//+------------------------------------------------------------------+
//| Execute CLOSE order                                               |
//+------------------------------------------------------------------+
void ExecuteCloseOrder(string strategy_id) {
   // Find the position by strategy_id
   int ticket = -1;
   int posIndex = -1;
   
   for (int i = 0; i < positionCount; i++) {
      if (openPositions[i].strategy_id == strategy_id) {
         ticket = openPositions[i].ticket;
         posIndex = i;
         break;
      }
   }
   
   if (ticket < 0) {
      Print("No open position found for strategy: ", strategy_id);
      return;
   }
   
   // Select and close the order
   if (!OrderSelect(ticket, SELECT_BY_TICKET)) {
      Print("Failed to select order #", ticket);
      return;
   }
   
   // Get the order's instrument and use its market prices
   string orderInstrument = OrderSymbol();
   int orderDigits = (int)MarketInfo(orderInstrument, MODE_DIGITS);
   
   double closePrice;
   if (OrderType() == OP_BUY) {
      closePrice = MarketInfo(orderInstrument, MODE_BID);
   } else {
      closePrice = MarketInfo(orderInstrument, MODE_ASK);
   }
   
   closePrice = NormalizeDouble(closePrice, orderDigits);
   
   bool closed = OrderClose(ticket, OrderLots(), closePrice, Slippage, clrGray);
   
   if (closed) {
      Print("Order closed successfully: Ticket #", ticket);
      
      // Calculate profit/loss
      double grossProfit = OrderProfit();
      double commission = OrderCommission();
      double swap = OrderSwap();
      double netProfit = grossProfit + commission + swap;
      
      // Update closed P/L tracking for this strategy
      UpdateClosedPL(strategy_id, netProfit);
      
      // Task 3.9: Send TRADE_UPDATE (CLOSE)
      SendTradeUpdate("CLOSE", ticket, strategy_id, closePrice, grossProfit, netProfit);
      
      // Remove from tracking
      if (posIndex >= 0 && posIndex < positionCount - 1) {
         // Shift array
         for (int i = posIndex; i < positionCount - 1; i++) {
            openPositions[i] = openPositions[i + 1];
         }
      }
      positionCount--;
   } else {
      int error = GetLastError();
      Print("Failed to close order: Error ", error, " - ", ErrorDescription(error));
   }
}

//+------------------------------------------------------------------+
//| Task 3.9: Send TRADE_UPDATE message to Python                     |
//+------------------------------------------------------------------+
void SendTradeUpdate(string eventType, int ticket, string strategy_id, double price, double grossProfit, double netProfit, double requested_price = 0) {
   if (!glbClientSocket || !glbClientSocket.IsSocketConnected()) return;
   
   if (!OrderSelect(ticket, SELECT_BY_TICKET)) return;
   
   string timestamp = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
   StringReplace(timestamp, ".", "-");
   StringReplace(timestamp, " ", "T");
   
   double commission = OrderCommission();
   double swap = OrderSwap();
   double slippage = MathAbs(price - OrderOpenPrice()) * Point;
   
   // Get the instrument from the order
   string orderInstrument = OrderSymbol();
   int orderDigits = (int)MarketInfo(orderInstrument, MODE_DIGITS);
   
   // Build JSON message
   string jsonMsg = "{";
   jsonMsg += "\"type\":\"TRADE_UPDATE\",";
   jsonMsg += "\"event_type\":\"" + eventType + "\",";
   jsonMsg += "\"trade_id\":\"MT4_" + IntegerToString(ticket) + "\",";
   jsonMsg += "\"strategy_id\":\"" + strategy_id + "\",";
   jsonMsg += "\"instrument\":\"" + orderInstrument + "\",";
   jsonMsg += "\"timestamp\":\"" + timestamp + "\",";
   jsonMsg += "\"price\":" + DoubleToString(price, orderDigits) + ",";
   jsonMsg += "\"commission\":" + DoubleToString(commission, 2) + ",";
   jsonMsg += "\"swap\":" + DoubleToString(swap, 2) + ",";
   jsonMsg += "\"slippage\":" + DoubleToString(slippage, orderDigits);
   
   // Include requested price for slippage calculation (Issue 3)
   if (requested_price > 0) {
      jsonMsg += ",\"requested_price\":" + DoubleToString(requested_price, orderDigits);
   }
   
   if (eventType == "CLOSE") {
      jsonMsg += ",\"gross_profit\":" + DoubleToString(grossProfit, 2);
      jsonMsg += ",\"net_profit\":" + DoubleToString(netProfit, 2);
   }
   
   jsonMsg += "}\r\n";
   
   if (glbClientSocket.Send(jsonMsg)) {
      Print("Sent TRADE_UPDATE: ", eventType, " for ticket #", ticket);
   } else {
      Print("Failed to send TRADE_UPDATE");
   }
}

//+------------------------------------------------------------------+
//| Send trade error notification                                      |
//+------------------------------------------------------------------+
void SendTradeError(string strategy_id, int errorCode) {
   if (!glbClientSocket || !glbClientSocket.IsSocketConnected()) return;
   
   string errorMsg = "Order execution failed: " + ErrorDescription(errorCode);
   
   string jsonMsg = CreateSimpleJson(
      "type", "ERROR",
      "message", errorMsg,
      "code", IntegerToString(errorCode),
      "strategy_id", strategy_id
   );
   
   jsonMsg += "\r\n";
   glbClientSocket.Send(jsonMsg);
}

//+------------------------------------------------------------------+
//| Get timeframe period                                               |
//+------------------------------------------------------------------+
int GetTimeframePeriod() {
   if (Timeframe == "M1") return PERIOD_M1;
   if (Timeframe == "M5") return PERIOD_M5;
   if (Timeframe == "M15") return PERIOD_M15;
   if (Timeframe == "M30") return PERIOD_M30;
   if (Timeframe == "H1") return PERIOD_H1;
   if (Timeframe == "H4") return PERIOD_H4;
   if (Timeframe == "D1") return PERIOD_D1;
   if (Timeframe == "W1") return PERIOD_W1;
   if (Timeframe == "MN1") return PERIOD_MN1;
   
   return PERIOD_H4; // Default
}

//+------------------------------------------------------------------+
//| Error description helper                                           |
//+------------------------------------------------------------------+
string ErrorDescription(int errorCode) {
   switch(errorCode) {
      case 0:    return "No error";
      case 1:    return "No error, but result is unknown";
      case 2:    return "Common error";
      case 3:    return "Invalid trade parameters";
      case 4:    return "Trade server is busy";
      case 5:    return "Old version of the client terminal";
      case 6:    return "No connection with trade server";
      case 7:    return "Not enough rights";
      case 8:    return "Too frequent requests";
      case 9:    return "Malfunctional trade operation";
      case 64:   return "Account disabled";
      case 65:   return "Invalid account";
      case 128:  return "Trade timeout";
      case 129:  return "Invalid price";
      case 130:  return "Invalid stops";
      case 131:  return "Invalid trade volume";
      case 132:  return "Market is closed";
      case 133:  return "Trade is disabled";
      case 134:  return "Not enough money";
      case 135:  return "Price changed";
      case 136:  return "Off quotes";
      case 137:  return "Broker is busy";
      case 138:  return "Requote";
      case 139:  return "Order is locked";
      case 140:  return "Buy orders only allowed";
      case 141:  return "Too many requests";
      case 145:  return "Modification denied";
      case 146:  return "Trade context is busy";
      case 147:  return "Expiration denied by broker";
      case 148:  return "Too many open orders";
      default:   return "Unknown error";
   }
}

//+------------------------------------------------------------------+
//| Handle CONFIG message from Python server                          |
//+------------------------------------------------------------------+
void HandleConfigMessage(JsonKeyValue &pairs[]) {
   Print("=== CONFIG message received from Python server ===");
   
   // Parse instruments string from JSON
   // Format: {"type":"CONFIG","instruments":"NDX:M1;EURUSD:H4","count":"2","account_info_interval":"15","request_history":"true"}
   
   string instrumentsStr = GetJsonValue(pairs, "instruments");
   string countStr = GetJsonValue(pairs, "count");
   string intervalStr = GetJsonValue(pairs, "account_info_interval");
   string requestHistoryStr = GetJsonValue(pairs, "request_history");
   
   if (instrumentsStr == "") {
      Print("ERROR: No instruments found in CONFIG message");
      return;
   }
   
   // Update account info interval if provided
   if (intervalStr != "") {
      accountInfoInterval = (int)StringToInteger(intervalStr);
      Print("  Account info interval set to: ", accountInfoInterval, " seconds");
   }
   
   int expectedCount = (int)StringToInteger(countStr);
   bool requestHistory = (requestHistoryStr == "true");
   Print("  Expected ", expectedCount, " instruments");
   Print("  Request historical data: ", requestHistory);
   
   // Reset configuration
   ArrayResize(configuredInstruments, 0);
   configCount = 0;
   
   // Parse instruments string: "NDX:M1;EURUSD:H4"
   string instruments[];
   int numInstruments = StringSplit(instrumentsStr, ';', instruments);
   
   for (int i = 0; i < numInstruments; i++) {
      string inst = instruments[i];
      
      // Split by colon to get symbol and timeframe
      string parts[];
      int numParts = StringSplit(inst, ':', parts);
      
      if (numParts != 2) {
         Print("ERROR: Invalid instrument format: ", inst);
         continue;
      }
      
      string symbol = parts[0];
      string timeframe = parts[1];
      
      // Add to configuration
      int idx = ArraySize(configuredInstruments);
      ArrayResize(configuredInstruments, idx + 1);
      configuredInstruments[idx].symbol = symbol;
      configuredInstruments[idx].timeframe = timeframe;
      configuredInstruments[idx].lastBarTime = 0;
      configuredInstruments[idx].chartId = 0;
      
      configCount++;
      
      Print("  Configured: ", symbol, " @ ", timeframe);
   }
   
   if (configCount != expectedCount) {
      Print("WARNING: Expected ", expectedCount, " instruments but parsed ", configCount);
   }
   
   configReceived = true;
   Print("=== Configuration complete: ", configCount, " instruments ===");
   
   // Open charts for configured instruments
   OpenRequiredCharts();
   
   // Send historical data if requested
   if (requestHistory) {
      Print("=== Sending historical data for all configured instruments ===");
      for (int i = 0; i < configCount; i++) {
         SendHistoricalBars(configuredInstruments[i].symbol, configuredInstruments[i].timeframe, 500);
      }
      Print("=== Historical data send complete ===");
   }
}

//+------------------------------------------------------------------+
//| Open charts for all configured instruments                        |
//+------------------------------------------------------------------+
void OpenRequiredCharts() {
   Print("=== Opening required charts ===");
   
   for (int i = 0; i < configCount; i++) {
      string symbol = configuredInstruments[i].symbol;
      string tf = configuredInstruments[i].timeframe;
      
      // Convert timeframe string to ENUM_TIMEFRAMES
      int period = StringToTimeframe(tf);
      if (period == 0) {
         Print("ERROR: Invalid timeframe: ", tf);
         continue;
      }
      
      // Check if chart already exists
      long chartId = ChartFirst();
      bool chartExists = false;
      
      while (chartId >= 0) {
         if (ChartSymbol(chartId) == symbol && ChartPeriod(chartId) == period) {
            Print("  Chart already exists: ", symbol, " ", tf, " (ID: ", chartId, ")");
            configuredInstruments[i].chartId = chartId;
            chartExists = true;
            break;
         }
         chartId = ChartNext(chartId);
      }
      
      // Open new chart if doesn't exist
      if (!chartExists) {
         long newChartId = ChartOpen(symbol, period);
         if (newChartId > 0) {
            configuredInstruments[i].chartId = newChartId;
            Print("  Opened new chart: ", symbol, " ", tf, " (ID: ", newChartId, ")");
         } else {
            Print("  ERROR: Failed to open chart for ", symbol, " ", tf);
         }
      }
   }
   
   Print("=== Chart setup complete ===");
}

//+------------------------------------------------------------------+
//| Convert timeframe string to period constant                       |
//+------------------------------------------------------------------+
int StringToTimeframe(string tf) {
   if (tf == "M1")  return PERIOD_M1;
   if (tf == "M5")  return PERIOD_M5;
   if (tf == "M15") return PERIOD_M15;
   if (tf == "M30") return PERIOD_M30;
   if (tf == "H1")  return PERIOD_H1;
   if (tf == "H4")  return PERIOD_H4;
   if (tf == "D1")  return PERIOD_D1;
   if (tf == "W1")  return PERIOD_W1;
   if (tf == "MN1") return PERIOD_MN1;
   return 0;
}

//+------------------------------------------------------------------+
//| Send historical bars for an instrument                            |
//+------------------------------------------------------------------+
void SendHistoricalBars(string symbol, string timeframe, int barCount) {
   if (!glbClientSocket || !glbClientSocket.IsSocketConnected()) {
      Print("ERROR: Cannot send historical data - not connected");
      return;
   }
   
   Print("Sending ", barCount, " historical bars for ", symbol, " ", timeframe);
   
   // Convert timeframe string to period
   int period = StringToTimeframe(timeframe);
   
   // Send bars from oldest to newest (reverse order from most recent)
   int sentCount = 0;
   for (int i = barCount - 1; i >= 0; i--) {
      datetime barTime = iTime(symbol, period, i);
      
      // Skip if bar time is invalid
      if (barTime == 0) continue;
      
      double openPrice = iOpen(symbol, period, i);
      double highPrice = iHigh(symbol, period, i);
      double lowPrice = iLow(symbol, period, i);
      double closePrice = iClose(symbol, period, i);
      long volume = iVolume(symbol, period, i);
      
      // Format timestamp as ISO 8601
      string timestamp = TimeToString(barTime, TIME_DATE|TIME_SECONDS);
      StringReplace(timestamp, ".", "-");
      StringReplace(timestamp, " ", "T");
      
      // Get symbol digits
      int digits = (int)MarketInfo(symbol, MODE_DIGITS);
      
      // Create MARKET_DATA message with is_historical flag
      string jsonMsg = CreateSimpleJson(
         "type", "MARKET_DATA",
         "instrument", symbol,
         "timeframe", timeframe,
         "timestamp", timestamp,
         "open", DoubleToString(openPrice, digits),
         "high", DoubleToString(highPrice, digits),
         "low", DoubleToString(lowPrice, digits),
         "close", DoubleToString(closePrice, digits),
         "volume", IntegerToString(volume),
         "is_historical", "true"
      );
      
      // DEBUG: Print first message to verify is_historical is included
      if (sentCount == 0) {
         Print("DEBUG: First historical bar JSON: ", jsonMsg);
      }
      
      jsonMsg += "\r\n";
      
      if (glbClientSocket.Send(jsonMsg)) {
         sentCount++;
      } else {
         Print("ERROR: Failed to send historical bar ", i);
         break;
      }
      
      // Small delay to avoid overwhelming the socket
      if (sentCount % 50 == 0) {
         Sleep(10);  // 10ms pause every 50 bars
      }
   }
   
   Print("Sent ", sentCount, " / ", barCount, " historical bars for ", symbol, " ", timeframe);
}

//+------------------------------------------------------------------+
