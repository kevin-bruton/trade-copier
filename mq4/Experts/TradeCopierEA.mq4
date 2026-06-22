//+------------------------------------------------------------------+
//|                                              TradeCopierEA.mq4   |
//|                                     Copyright 2025, Kevin Bruton |
//|                                                                   |
//| MT4 client EA for the Trade Copier Python server.                 |
//| Connects as a TCP socket client; reports trades and executes      |
//| copy/close commands from the server.                              |
//+------------------------------------------------------------------+
#property strict
#include <Sockets.mqh>
#include <SimpleJson.mqh>
#include <stdlib.mqh>   // ErrorDescription()

//--- Input parameters
input string  ServerHost               = "127.0.0.1";  // Python server host/IP
input ushort  ServerPort               = 9000;         // Python server port
input int     HeartbeatIntervalSec     = 30;           // Heartbeat interval (seconds)
input int     AccountUpdateIntervalSec = 15;           // Account update interval (seconds)
input int     TimerIntervalMs          = 100;          // Poll interval (milliseconds)
input int     Slippage                 = 3;            // Slippage in points

//--- Global state
ClientSocket* glbSocket        = NULL;
bool          isConnected      = false;
datetime      lastHeartbeat    = 0;
datetime      lastAccountUpdate = 0;

//--- Tracked open positions
struct TrackedPosition {
   long     ticket;
   string   symbol;
   int      direction;   // OP_BUY or OP_SELL
   double   lots;
   double   openPrice;
   double   sl;
   double   tp;
   int      magic;
   string   comment;
   datetime openTime;
};

TrackedPosition trackedPositions[];
int             trackedCount = 0;

//+------------------------------------------------------------------+
//| Format datetime as "YYYY-MM-DDTHH:MM:SS"                         |
//+------------------------------------------------------------------+
string FormatTime(datetime t) {
   string ts = TimeToString(t, TIME_DATE | TIME_SECONDS);
   StringReplace(ts, ".", "-");
   StringReplace(ts, " ", "T");
   return ts;
}

//+------------------------------------------------------------------+
//| Return true if str begins with prefix                            |
//+------------------------------------------------------------------+
bool StartsWith(string str, string prefix) {
   return StringSubstr(str, 0, StringLen(prefix)) == prefix;
}

//+------------------------------------------------------------------+
//| Create or update the on-chart status label                       |
//+------------------------------------------------------------------+
void UpdateStatusLabel(bool connected) {
   string text;
   color  col;
   if (connected) {
      text = "⬤ TradeCopier  CONNECTED   " + IntegerToString(AccountNumber());
      col  = clrLimeGreen;
   } else {
      text = "⬤ TradeCopier  DISCONNECTED";
      col  = clrRed;
   }

   if (ObjectFind(0, "TC_Status") < 0) {
      ObjectCreate(0, "TC_Status", OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, "TC_Status", OBJPROP_CORNER,    CORNER_RIGHT_UPPER);
      ObjectSetInteger(0, "TC_Status", OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, "TC_Status", OBJPROP_YDISTANCE, 20);
      ObjectSetString( 0, "TC_Status", OBJPROP_FONT,      "Consolas");
      ObjectSetInteger(0, "TC_Status", OBJPROP_FONTSIZE,  10);
   }
   ObjectSetString( 0, "TC_Status", OBJPROP_TEXT,  text);
   ObjectSetInteger(0, "TC_Status", OBJPROP_COLOR, col);
   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Serialise msg and send over the socket with \r\n terminator      |
//+------------------------------------------------------------------+
bool SendMsg(string msg) {
   if (!glbSocket) return false;
   bool ok = glbSocket.Send(msg + "\r\n");
   if (!ok) {
      Print("TradeCopierEA MT4: socket send failed, error=", glbSocket.GetLastSocketError());
   }
   return ok;
}

//+------------------------------------------------------------------+
//| Return index of ticket in trackedPositions[], or -1              |
//+------------------------------------------------------------------+
int FindTracked(long ticket) {
   for (int i = 0; i < trackedCount; i++) {
      if (trackedPositions[i].ticket == ticket) return i;
   }
   return -1;
}

//+------------------------------------------------------------------+
//| Remove element at idx by shifting array down                     |
//+------------------------------------------------------------------+
void RemoveTracked(int idx) {
   for (int i = idx; i < trackedCount - 1; i++) {
      trackedPositions[i] = trackedPositions[i + 1];
   }
   trackedCount--;
   ArrayResize(trackedPositions, trackedCount);
}

//+------------------------------------------------------------------+
//| Append a new entry to trackedPositions[]                         |
//+------------------------------------------------------------------+
void AddTracked(long ticket, string symbol, int direction, double lots,
                double openPrice, double sl, double tp, int magic,
                string comment, datetime openTime) {
   ArrayResize(trackedPositions, trackedCount + 1);
   trackedPositions[trackedCount].ticket    = ticket;
   trackedPositions[trackedCount].symbol    = symbol;
   trackedPositions[trackedCount].direction = direction;
   trackedPositions[trackedCount].lots      = lots;
   trackedPositions[trackedCount].openPrice = openPrice;
   trackedPositions[trackedCount].sl        = sl;
   trackedPositions[trackedCount].tp        = tp;
   trackedPositions[trackedCount].magic     = magic;
   trackedPositions[trackedCount].comment   = comment;
   trackedPositions[trackedCount].openTime  = openTime;
   trackedCount++;
}

//+------------------------------------------------------------------+
//| Populate trackedPositions from current open orders on attach.    |
//| Called once from OnInit — does NOT send TRADE_OPENED messages.   |
//+------------------------------------------------------------------+
void InitTrackedPositions() {
   trackedCount = 0;
   ArrayResize(trackedPositions, 0);

   for (int i = 0; i < OrdersTotal(); i++) {
      if (!OrderSelect(i, SELECT_BY_POS)) continue;
      if (OrderType() >= 2) continue;   // market orders only (OP_BUY=0, OP_SELL=1)

      AddTracked(
         OrderTicket(),
         OrderSymbol(),
         OrderType(),
         OrderLots(),
         OrderOpenPrice(),
         OrderStopLoss(),
         OrderTakeProfit(),
         OrderMagicNumber(),
         OrderComment(),
         OrderOpenTime()
      );
   }
}

//+------------------------------------------------------------------+
//| Send REGISTER message                                            |
//+------------------------------------------------------------------+
void SendRegister() {
   string accountType = IsDemo() ? "demo" : "real";
   string msg = CreateSimpleJson(
      "type",          "REGISTER",
      "terminal_path", TerminalPath(),
      "platform",      "MT4",
      "broker",        AccountCompany(),
      "account",       IntegerToString(AccountNumber()),
      "account_type",  accountType,
      "currency",      AccountCurrency(),
      "leverage",      IntegerToString(AccountLeverage()),
      "balance",       DoubleToString(AccountBalance(),    2),
      "equity",        DoubleToString(AccountEquity(),     2),
      "margin",        DoubleToString(AccountMargin(),     2),
      "free_margin",   DoubleToString(AccountFreeMargin(), 2)
   );
   if (SendMsg(msg)) {
      Print("TradeCopierEA MT4: REGISTER sent for ", TerminalPath());
   } else {
      Print("TradeCopierEA MT4: REGISTER send failed for ", TerminalPath());
   }
}

//+------------------------------------------------------------------+
//| Build semicolon-separated positions string for POSITIONS_SNAPSHOT|
//+------------------------------------------------------------------+
string BuildPositionsString(int &outCount) {
   string result = "";
   outCount = 0;

   for (int i = 0; i < OrdersTotal(); i++) {
      if (!OrderSelect(i, SELECT_BY_POS)) continue;
      if (OrderType() >= 2) continue;
      if (StartsWith(OrderComment(), "COPY_")) continue;

      int    digits = (int)MarketInfo(OrderSymbol(), MODE_DIGITS);
      string dir    = (OrderType() == OP_BUY) ? "buy" : "sell";

      if (outCount > 0) result += ";";
      result += IntegerToString(OrderTicket())                                    + "|"
             +  OrderSymbol()                                                     + "|"
             +  dir                                                                + "|"
             +  DoubleToString(OrderLots(),        2)                             + "|"
             +  DoubleToString(OrderOpenPrice(),   digits)                        + "|"
             +  DoubleToString(OrderStopLoss(),    digits)                        + "|"
             +  DoubleToString(OrderTakeProfit(),  digits)                        + "|"
             +  IntegerToString(OrderMagicNumber())                               + "|"
             +  FormatTime(OrderOpenTime())                                        + "|"
             +  OrderComment();
      outCount++;
   }
   return result;
}

//+------------------------------------------------------------------+
//| Send POSITIONS_SNAPSHOT message                                  |
//+------------------------------------------------------------------+
void SendPositionsSnapshot() {
   int    count;
   string positions = BuildPositionsString(count);
   string msg = CreateSimpleJson(
      "type",      "POSITIONS_SNAPSHOT",
      "count",     IntegerToString(count),
      "positions", positions
   );
   SendMsg(msg);
   Print("TradeCopierEA MT4: POSITIONS_SNAPSHOT sent, count=", count);
}

//+------------------------------------------------------------------+
//| Detect opened/closed positions and notify the server             |
//+------------------------------------------------------------------+
void CheckPositionChanges() {
   // --- Build current open-position snapshot (all positions incl. COPY_) ---
   long     curTickets[];
   string   curSymbols[];
   int      curDirections[];
   double   curLots[];
   double   curOpenPrices[];
   double   curSLs[];
   double   curTPs[];
   int      curMagics[];
   string   curComments[];
   datetime curOpenTimes[];
   int      curCount = 0;

   for (int i = 0; i < OrdersTotal(); i++) {
      if (!OrderSelect(i, SELECT_BY_POS)) continue;
      if (OrderType() >= 2) continue;

      ArrayResize(curTickets,    curCount + 1);
      ArrayResize(curSymbols,    curCount + 1);
      ArrayResize(curDirections, curCount + 1);
      ArrayResize(curLots,       curCount + 1);
      ArrayResize(curOpenPrices, curCount + 1);
      ArrayResize(curSLs,        curCount + 1);
      ArrayResize(curTPs,        curCount + 1);
      ArrayResize(curMagics,     curCount + 1);
      ArrayResize(curComments,   curCount + 1);
      ArrayResize(curOpenTimes,  curCount + 1);

      curTickets[curCount]    = OrderTicket();
      curSymbols[curCount]    = OrderSymbol();
      curDirections[curCount] = OrderType();
      curLots[curCount]       = OrderLots();
      curOpenPrices[curCount] = OrderOpenPrice();
      curSLs[curCount]        = OrderStopLoss();
      curTPs[curCount]        = OrderTakeProfit();
      curMagics[curCount]     = OrderMagicNumber();
      curComments[curCount]   = OrderComment();
      curOpenTimes[curCount]  = OrderOpenTime();
      curCount++;
   }

   // --- Detect new positions (in current but not yet tracked) ---
   for (int i = 0; i < curCount; i++) {
      if (FindTracked(curTickets[i]) >= 0) continue;

      // Always add to tracked so we don't re-detect on the next poll
      AddTracked(
         curTickets[i], curSymbols[i], curDirections[i], curLots[i],
         curOpenPrices[i], curSLs[i], curTPs[i], curMagics[i],
         curComments[i], curOpenTimes[i]
      );

      // COPY_ positions must NOT generate TRADE_OPENED (loop-prevention)
      if (StartsWith(curComments[i], "COPY_")) continue;

      string dir    = (curDirections[i] == OP_BUY) ? "buy" : "sell";
      int    digits = (int)MarketInfo(curSymbols[i], MODE_DIGITS);

      string msg = CreateSimpleJson(
         "type",       "TRADE_OPENED",
         "ticket",     IntegerToString(curTickets[i]),
         "symbol",     curSymbols[i],
         "direction",  dir,
         "lots",       DoubleToString(curLots[i],       2),
         "open_price", DoubleToString(curOpenPrices[i], digits),
         "sl",         DoubleToString(curSLs[i],        digits),
         "tp",         DoubleToString(curTPs[i],        digits),
         "magic",      IntegerToString(curMagics[i]),
         "open_time",  FormatTime(curOpenTimes[i]),
         "comment",    curComments[i]
      );
      SendMsg(msg);
      Print("TradeCopierEA MT4: TRADE_OPENED ticket=", curTickets[i], " symbol=", curSymbols[i]);
   }

   // --- Detect closed positions (in tracked but gone from current) ---
   // Capture initial count; positions added in the loop above won't be "missing"
   int initialTracked = trackedCount;
   for (int i = initialTracked - 1; i >= 0; i--) {
      bool stillOpen = false;
      for (int j = 0; j < curCount; j++) {
         if (curTickets[j] == trackedPositions[i].ticket) { stillOpen = true; break; }
      }
      if (stillOpen) continue;

      long   ticket    = trackedPositions[i].ticket;
      string symbol    = trackedPositions[i].symbol;
      int    direction = trackedPositions[i].direction;
      double lots      = trackedPositions[i].lots;
      int    magic     = trackedPositions[i].magic;

      double   closePrice = 0.0;
      double   profit     = 0.0;
      datetime closeTime  = TimeCurrent();

      if (OrderSelect((int)ticket, SELECT_BY_TICKET)) {
         closePrice = OrderClosePrice();
         profit     = OrderProfit();
         closeTime  = OrderCloseTime();
      }

      RemoveTracked(i);

      string dir    = (direction == OP_BUY) ? "buy" : "sell";
      int    digits = (int)MarketInfo(symbol, MODE_DIGITS);

      string msg = CreateSimpleJson(
         "type",        "TRADE_CLOSED",
         "ticket",      IntegerToString(ticket),
         "symbol",      symbol,
         "direction",   dir,
         "lots",        DoubleToString(lots,       2),
         "close_price", DoubleToString(closePrice, digits),
         "profit",      DoubleToString(profit,     2),
         "magic",       IntegerToString(magic),
         "close_time",  FormatTime(closeTime)
      );
      SendMsg(msg);
      Print("TradeCopierEA MT4: TRADE_CLOSED ticket=", ticket, " symbol=", symbol);
   }
}

//+------------------------------------------------------------------+
//| Send ACCOUNT_UPDATE if interval has elapsed                      |
//+------------------------------------------------------------------+
void MaybeSendAccountUpdate() {
   if (TimeCurrent() - lastAccountUpdate < AccountUpdateIntervalSec) return;
   lastAccountUpdate = TimeCurrent();

   string msg = CreateSimpleJson(
      "type",        "ACCOUNT_UPDATE",
      "balance",     DoubleToString(AccountBalance(),    2),
      "equity",      DoubleToString(AccountEquity(),     2),
      "margin",      DoubleToString(AccountMargin(),     2),
      "free_margin", DoubleToString(AccountFreeMargin(), 2)
   );
   SendMsg(msg);
}

//+------------------------------------------------------------------+
//| Send HEARTBEAT if interval has elapsed                           |
//+------------------------------------------------------------------+
void MaybeSendHeartbeat() {
   if (TimeCurrent() - lastHeartbeat < HeartbeatIntervalSec) return;
   lastHeartbeat = TimeCurrent();

   string msg = CreateSimpleJson(
      "type",      "HEARTBEAT",
      "timestamp", FormatTime(TimeCurrent())
   );
   SendMsg(msg);
}

//+------------------------------------------------------------------+
//| Open a copied trade on behalf of the Python server               |
//+------------------------------------------------------------------+
void ExecuteCopyTrade(string copy_id, string symbol, string direction,
                      double lots, double sl, double tp, int magic) {
   // Ensure symbol is visible in Market Watch
   SymbolSelect(symbol, true);

   int    digits    = (int)MarketInfo(symbol, MODE_DIGITS);
   int    orderType;
   double price;

   if (direction == "buy") {
      orderType = OP_BUY;
      price     = MarketInfo(symbol, MODE_ASK);
   } else {
      orderType = OP_SELL;
      price     = MarketInfo(symbol, MODE_BID);
   }

   if (price <= 0) {
      string errMsg = CreateSimpleJson(
         "type",       "COPY_RESULT",
         "copy_id",    copy_id,
         "success",    "false",
         "ticket",     "0",
         "open_price", "0",
         "error",      "Invalid price (market closed?)"
      );
      SendMsg(errMsg);
      return;
   }

   // Clamp lots to broker limits and round down to step
   double minLot  = MarketInfo(symbol, MODE_MINLOT);
   double maxLot  = MarketInfo(symbol, MODE_MAXLOT);
   double lotStep = MarketInfo(symbol, MODE_LOTSTEP);
   if (lots < minLot) lots = minLot;
   if (lots > maxLot) lots = maxLot;
   lots = NormalizeDouble(MathFloor(lots / lotStep) * lotStep, 2);

   // Normalise prices
   price = NormalizeDouble(price, digits);
   if (sl > 0) sl = NormalizeDouble(sl, digits);
   if (tp > 0) tp = NormalizeDouble(tp, digits);

   // Comment: "COPY_" + 26 hex chars = 31 chars (MT4 limit)
   string comment = "COPY_" + StringSubstr(copy_id, 0, 26);

   int ticket = OrderSend(
      symbol, orderType, lots, price, Slippage,
      sl > 0 ? sl : 0,
      tp > 0 ? tp : 0,
      comment, magic, 0,
      (orderType == OP_BUY) ? clrBlue : clrRed
   );

   if (ticket > 0) {
      string fillPrice = DoubleToString(price, digits);
      if (OrderSelect(ticket, SELECT_BY_TICKET))
         fillPrice = DoubleToString(OrderOpenPrice(), digits);

      string resultMsg = CreateSimpleJson(
         "type",       "COPY_RESULT",
         "copy_id",    copy_id,
         "success",    "true",
         "ticket",     IntegerToString(ticket),
         "open_price", fillPrice,
         "error",      ""
      );
      SendMsg(resultMsg);

      // Track immediately so CheckPositionChanges never fires TRADE_OPENED for it
      if (OrderSelect(ticket, SELECT_BY_TICKET)) {
         AddTracked(
            ticket, OrderSymbol(), OrderType(), OrderLots(),
            OrderOpenPrice(), OrderStopLoss(), OrderTakeProfit(),
            OrderMagicNumber(), OrderComment(), OrderOpenTime()
         );
      }
      Print("TradeCopierEA MT4: COPY_RESULT success ticket=", ticket);
   } else {
      int    error  = GetLastError();
      string errStr = ErrorDescription(error);
      string resultMsg = CreateSimpleJson(
         "type",       "COPY_RESULT",
         "copy_id",    copy_id,
         "success",    "false",
         "ticket",     "0",
         "open_price", "0",
         "error",      errStr
      );
      SendMsg(resultMsg);
      Print("TradeCopierEA MT4: COPY_RESULT failed error=", error, " ", errStr);
   }
}

//+------------------------------------------------------------------+
//| Close a copied trade on behalf of the Python server              |
//+------------------------------------------------------------------+
void ExecuteCloseTrade(string copy_id, long ticket) {
   if (!OrderSelect((int)ticket, SELECT_BY_TICKET)) {
      string msg = CreateSimpleJson(
         "type",        "CLOSE_RESULT",
         "copy_id",     copy_id,
         "success",     "false",
         "close_price", "0",
         "error",       "Ticket not found"
      );
      SendMsg(msg);
      return;
   }

   if (OrderCloseTime() > 0) {
      string msg = CreateSimpleJson(
         "type",        "CLOSE_RESULT",
         "copy_id",     copy_id,
         "success",     "false",
         "close_price", "0",
         "error",       "Position already closed"
      );
      SendMsg(msg);
      return;
   }

   string symbol = OrderSymbol();
   double lots   = OrderLots();
   int    digits = (int)MarketInfo(symbol, MODE_DIGITS);

   double price = (OrderType() == OP_BUY)
                ? MarketInfo(symbol, MODE_BID)
                : MarketInfo(symbol, MODE_ASK);
   price = NormalizeDouble(price, digits);

   bool closed = OrderClose((int)ticket, lots, price, Slippage, clrGray);

   if (closed) {
      string closePrice = DoubleToString(price, digits);
      if (OrderSelect((int)ticket, SELECT_BY_TICKET))
         closePrice = DoubleToString(OrderClosePrice(), digits);

      string msg = CreateSimpleJson(
         "type",        "CLOSE_RESULT",
         "copy_id",     copy_id,
         "success",     "true",
         "close_price", closePrice,
         "error",       ""
      );
      SendMsg(msg);

      int idx = FindTracked(ticket);
      if (idx >= 0) RemoveTracked(idx);
      Print("TradeCopierEA MT4: CLOSE_RESULT success ticket=", ticket);
   } else {
      int    error  = GetLastError();
      string errStr = ErrorDescription(error);
      string msg = CreateSimpleJson(
         "type",        "CLOSE_RESULT",
         "copy_id",     copy_id,
         "success",     "false",
         "close_price", "0",
         "error",       errStr
      );
      SendMsg(msg);
      Print("TradeCopierEA MT4: CLOSE_RESULT failed error=", error, " ", errStr);
   }
}

//+------------------------------------------------------------------+
//| Dispatch a single incoming JSON message                          |
//+------------------------------------------------------------------+
void RouteMessage(string rawMsg) {
   JsonKeyValue pairs[];
   if (!ParseSimpleJson(rawMsg, pairs)) {
      Print("TradeCopierEA MT4: Failed to parse: ", rawMsg);
      return;
   }

   string type = GetJsonValue(pairs, "type");

   if (type == "COPY_TRADE") {
      ExecuteCopyTrade(
         GetJsonValue(pairs, "copy_id"),
         GetJsonValue(pairs, "symbol"),
         GetJsonValue(pairs, "direction"),
         StringToDouble(GetJsonValue(pairs, "lots")),
         StringToDouble(GetJsonValue(pairs, "sl")),
         StringToDouble(GetJsonValue(pairs, "tp")),
         (int)StringToInteger(GetJsonValue(pairs, "magic"))
      );
   }
   else if (type == "CLOSE_TRADE") {
      ExecuteCloseTrade(
         GetJsonValue(pairs, "copy_id"),
         StringToInteger(GetJsonValue(pairs, "ticket"))
      );
   }
   else if (type == "ACK_REGISTER") {
      Print("TradeCopierEA MT4: ACK_REGISTER confirmed path=",
            GetJsonValue(pairs, "terminal_path"));
   }
   else if (type == "HEARTBEAT") {
      // No action needed
   }
   else {
      Print("TradeCopierEA MT4: Unknown message type: ", type);
   }
}

//+------------------------------------------------------------------+
//| Drain all pending incoming messages from the socket              |
//+------------------------------------------------------------------+
void ProcessIncomingMessages() {
   if (!glbSocket) return;
   string msg;
   do {
      msg = glbSocket.Receive("\r\n");
      if (msg != "") RouteMessage(msg);
   } while (msg != "");
}

//+------------------------------------------------------------------+
//| Manage the socket connection state                               |
//+------------------------------------------------------------------+
void HandleConnection() {
   if (glbSocket == NULL) {
      glbSocket = new ClientSocket(ServerHost, ServerPort);

      if (glbSocket.IsSocketConnected()) {
         isConnected      = true;
         lastHeartbeat    = TimeCurrent();
         lastAccountUpdate = TimeCurrent();
         UpdateStatusLabel(true);
         Print("TradeCopierEA MT4: Connected to ", ServerHost, ":", ServerPort);
         SendRegister();
         SendPositionsSnapshot();
      } else {
         delete glbSocket;
         glbSocket = NULL;
      }
   }
   else if (!glbSocket.IsSocketConnected()) {
      isConnected = false;
      UpdateStatusLabel(false);
      Print("TradeCopierEA MT4: Connection lost, will retry");
      delete glbSocket;
      glbSocket = NULL;
   }
}

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit() {
   EventSetMillisecondTimer(TimerIntervalMs);
   UpdateStatusLabel(false);
   InitTrackedPositions();
   Print("TradeCopierEA MT4 initialised, tracking ", trackedCount, " existing positions");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   UpdateStatusLabel(false);

   if (glbSocket) {
      delete glbSocket;
      glbSocket = NULL;
   }

   ObjectDelete(0, "TC_Status");
   Print("TradeCopierEA MT4 deinitialised");
}

//+------------------------------------------------------------------+
//| Timer — fires every TimerIntervalMs milliseconds                 |
//+------------------------------------------------------------------+
void OnTimer() {
   HandleConnection();

   if (!isConnected) return;

   ProcessIncomingMessages();
   CheckPositionChanges();
   MaybeSendAccountUpdate();
   MaybeSendHeartbeat();
}
