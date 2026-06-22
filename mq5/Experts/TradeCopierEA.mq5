//+------------------------------------------------------------------+
//|                                              TradeCopierEA.mq5   |
//|                                     Copyright 2025, Kevin Bruton |
//|                                                                   |
//| MT5 client EA for the Trade Copier Python server.                 |
//| Connects as a TCP socket client; reports trades and executes      |
//| copy/close commands from the server.                              |
//| OnTrade() provides near-zero detection latency for position       |
//| changes; OnTimer() handles the connection loop and heartbeats.    |
//+------------------------------------------------------------------+
#property strict
#include <Sockets.mqh>
#include <SimpleJson.mqh>

//--- Input parameters
input string  ServerHost               = "127.0.0.1";  // Python server host/IP
input ushort  ServerPort               = 9000;         // Python server port
input int     HeartbeatIntervalSec     = 30;           // Heartbeat interval (seconds)
input int     AccountUpdateIntervalSec = 15;           // Account update interval (seconds)
input int     TimerIntervalMs          = 100;          // Poll interval (milliseconds)
input int     Slippage                 = 3;            // Slippage in points

//--- Global state
ClientSocket* glbSocket         = NULL;
bool          isConnected       = false;
datetime      lastHeartbeat     = 0;
datetime      lastAccountUpdate = 0;

//--- Tracked open positions
struct TrackedPosition {
   long     ticket;
   string   symbol;
   int      direction;   // POSITION_TYPE_BUY=0 or POSITION_TYPE_SELL=1
   double   lots;
   double   openPrice;
   double   sl;
   double   tp;
   long     magic;
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
      text = "⬤ TradeCopier  CONNECTED   "
           + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
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
   return glbSocket.Send(msg + "\r\n");
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
                double openPrice, double sl, double tp, long magic,
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
//| Populate trackedPositions from current open positions on attach. |
//| Called once from OnInit — does NOT send TRADE_OPENED messages.   |
//+------------------------------------------------------------------+
void InitTrackedPositions() {
   trackedCount = 0;
   ArrayResize(trackedPositions, 0);

   for (int i = 0; i < PositionsTotal(); i++) {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0) continue;

      AddTracked(
         (long)ticket,
         PositionGetString(POSITION_SYMBOL),
         (int)PositionGetInteger(POSITION_TYPE),
         PositionGetDouble(POSITION_VOLUME),
         PositionGetDouble(POSITION_PRICE_OPEN),
         PositionGetDouble(POSITION_SL),
         PositionGetDouble(POSITION_TP),
         PositionGetInteger(POSITION_MAGIC),
         PositionGetString(POSITION_COMMENT),
         (datetime)PositionGetInteger(POSITION_TIME)
      );
   }
}

//+------------------------------------------------------------------+
//| Send REGISTER message                                            |
//+------------------------------------------------------------------+
void SendRegister() {
   long tradeMode   = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   string accountType = (tradeMode == ACCOUNT_TRADE_MODE_REAL) ? "real" : "demo";

   string msg = CreateSimpleJson(
      "type",          "REGISTER",
      "terminal_path", TerminalInfoString(TERMINAL_PATH),
      "platform",      "MT5",
      "broker",        AccountInfoString(ACCOUNT_COMPANY),
      "account",       IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)),
      "account_type",  accountType,
      "currency",      AccountInfoString(ACCOUNT_CURRENCY),
      "leverage",      IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)),
      "balance",       DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),      2),
      "equity",        DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),       2),
      "margin",        DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),       2),
      "free_margin",   DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),  2)
   );
   SendMsg(msg);
   Print("TradeCopierEA MT5: REGISTER sent for ", TerminalInfoString(TERMINAL_PATH));
}

//+------------------------------------------------------------------+
//| Build semicolon-separated positions string for POSITIONS_SNAPSHOT|
//+------------------------------------------------------------------+
string BuildPositionsString(int &outCount) {
   string result = "";
   outCount = 0;

   for (int i = 0; i < PositionsTotal(); i++) {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0) continue;

      string comment = PositionGetString(POSITION_COMMENT);
      if (StartsWith(comment, "COPY_")) continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      int    digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      int    posType = (int)PositionGetInteger(POSITION_TYPE);
      string dir     = (posType == POSITION_TYPE_BUY) ? "buy" : "sell";

      if (outCount > 0) result += ";";
      result += IntegerToString((long)ticket)                                              + "|"
             +  symbol                                                                     + "|"
             +  dir                                                                        + "|"
             +  DoubleToString(PositionGetDouble(POSITION_VOLUME),      2)                + "|"
             +  DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),  digits)           + "|"
             +  DoubleToString(PositionGetDouble(POSITION_SL),          digits)           + "|"
             +  DoubleToString(PositionGetDouble(POSITION_TP),          digits)           + "|"
             +  IntegerToString(PositionGetInteger(POSITION_MAGIC))                       + "|"
             +  FormatTime((datetime)PositionGetInteger(POSITION_TIME))                   + "|"
             +  comment;
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
   Print("TradeCopierEA MT5: POSITIONS_SNAPSHOT sent, count=", count);
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
   long     curMagics[];
   string   curComments[];
   datetime curOpenTimes[];
   int      curCount = 0;

   for (int i = 0; i < PositionsTotal(); i++) {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0) continue;

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

      curTickets[curCount]    = (long)ticket;
      curSymbols[curCount]    = PositionGetString(POSITION_SYMBOL);
      curDirections[curCount] = (int)PositionGetInteger(POSITION_TYPE);
      curLots[curCount]       = PositionGetDouble(POSITION_VOLUME);
      curOpenPrices[curCount] = PositionGetDouble(POSITION_PRICE_OPEN);
      curSLs[curCount]        = PositionGetDouble(POSITION_SL);
      curTPs[curCount]        = PositionGetDouble(POSITION_TP);
      curMagics[curCount]     = PositionGetInteger(POSITION_MAGIC);
      curComments[curCount]   = PositionGetString(POSITION_COMMENT);
      curOpenTimes[curCount]  = (datetime)PositionGetInteger(POSITION_TIME);
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

      string dir    = (curDirections[i] == POSITION_TYPE_BUY) ? "buy" : "sell";
      int    digits = (int)SymbolInfoInteger(curSymbols[i], SYMBOL_DIGITS);

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
      Print("TradeCopierEA MT5: TRADE_OPENED ticket=", curTickets[i], " symbol=", curSymbols[i]);
   }

   // --- Detect closed positions (in tracked but gone from current) ---
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
      long   magic     = trackedPositions[i].magic;

      double   closePrice = 0.0;
      double   profit     = 0.0;
      datetime closeTime  = TimeCurrent();

      // Retrieve close details from deal history
      if (HistorySelectByPosition((ulong)ticket)) {
         int deals = HistoryDealsTotal();
         for (int d = deals - 1; d >= 0; d--) {
            ulong dealTicket = HistoryDealGetTicket(d);
            if (HistoryDealGetInteger(dealTicket, DEAL_ENTRY) == DEAL_ENTRY_OUT) {
               closePrice = HistoryDealGetDouble(dealTicket,  DEAL_PRICE);
               profit     = HistoryDealGetDouble(dealTicket,  DEAL_PROFIT);
               closeTime  = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
               break;
            }
         }
      }

      RemoveTracked(i);

      string dir    = (direction == POSITION_TYPE_BUY) ? "buy" : "sell";
      int    digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

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
      Print("TradeCopierEA MT5: TRADE_CLOSED ticket=", ticket, " symbol=", symbol);
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
      "balance",     DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),     2),
      "equity",      DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),      2),
      "margin",      DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),      2),
      "free_margin", DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2)
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
   if (!SymbolSelect(symbol, true)) {
      Print("TradeCopierEA MT5: SymbolSelect failed for ", symbol);
   }

   int    digits    = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   ENUM_ORDER_TYPE orderType;
   double price;

   if (direction == "buy") {
      orderType = ORDER_TYPE_BUY;
      price     = SymbolInfoDouble(symbol, SYMBOL_ASK);
   } else {
      orderType = ORDER_TYPE_SELL;
      price     = SymbolInfoDouble(symbol, SYMBOL_BID);
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
   double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if (lots < minLot) lots = minLot;
   if (lots > maxLot) lots = maxLot;
   lots = NormalizeDouble(MathFloor(lots / lotStep) * lotStep, 2);

   // Normalise prices
   price = NormalizeDouble(price, digits);
   if (sl > 0) sl = NormalizeDouble(sl, digits);
   if (tp > 0) tp = NormalizeDouble(tp, digits);

   // Comment: "COPY_" + full copy_id (no truncation needed in MT5)
   string comment = "COPY_" + copy_id;

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = symbol;
   request.volume    = lots;
   request.type      = orderType;
   request.price     = price;
   request.sl        = (sl > 0) ? sl : 0;
   request.tp        = (tp > 0) ? tp : 0;
   request.deviation = Slippage;
   request.magic     = magic;
   request.comment   = comment;

   bool sent = OrderSend(request, result);

   if (sent && result.retcode == TRADE_RETCODE_DONE) {
      // result.deal is the position ticket for a newly opened position
      ulong posTicket = result.deal;
      string fillPrice = DoubleToString(result.price > 0 ? result.price : price, digits);

      string resultMsg = CreateSimpleJson(
         "type",       "COPY_RESULT",
         "copy_id",    copy_id,
         "success",    "true",
         "ticket",     IntegerToString((long)posTicket),
         "open_price", fillPrice,
         "error",      ""
      );
      SendMsg(resultMsg);

      // Track immediately so CheckPositionChanges never fires TRADE_OPENED for it
      AddTracked(
         (long)posTicket, symbol,
         (direction == "buy") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL,
         lots, result.price > 0 ? result.price : price,
         sl > 0 ? sl : 0, tp > 0 ? tp : 0,
         magic, comment, TimeCurrent()
      );
      Print("TradeCopierEA MT5: COPY_RESULT success ticket=", posTicket);
   } else {
      string errStr = result.comment != "" ? result.comment
                                           : IntegerToString(result.retcode);
      string resultMsg = CreateSimpleJson(
         "type",       "COPY_RESULT",
         "copy_id",    copy_id,
         "success",    "false",
         "ticket",     "0",
         "open_price", "0",
         "error",      errStr
      );
      SendMsg(resultMsg);
      Print("TradeCopierEA MT5: COPY_RESULT failed retcode=", result.retcode,
            " ", result.comment);
   }
}

//+------------------------------------------------------------------+
//| Close a copied trade on behalf of the Python server              |
//+------------------------------------------------------------------+
void ExecuteCloseTrade(string copy_id, long ticket) {
   if (!PositionSelectByTicket((ulong)ticket)) {
      string msg = CreateSimpleJson(
         "type",        "CLOSE_RESULT",
         "copy_id",     copy_id,
         "success",     "false",
         "close_price", "0",
         "error",       "Ticket not found or already closed"
      );
      SendMsg(msg);
      return;
   }

   string symbol    = PositionGetString(POSITION_SYMBOL);
   double lots      = PositionGetDouble(POSITION_VOLUME);
   int    posType   = (int)PositionGetInteger(POSITION_TYPE);
   int    digits    = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   ENUM_ORDER_TYPE closeType;
   double          price;
   if (posType == POSITION_TYPE_BUY) {
      closeType = ORDER_TYPE_SELL;
      price     = SymbolInfoDouble(symbol, SYMBOL_BID);
   } else {
      closeType = ORDER_TYPE_BUY;
      price     = SymbolInfoDouble(symbol, SYMBOL_ASK);
   }
   price = NormalizeDouble(price, digits);

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = symbol;
   request.volume    = lots;
   request.type      = closeType;
   request.price     = price;
   request.deviation = Slippage;
   request.position  = (ulong)ticket;

   bool sent = OrderSend(request, result);

   if (sent && result.retcode == TRADE_RETCODE_DONE) {
      string closePrice = DoubleToString(result.price > 0 ? result.price : price, digits);

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
      Print("TradeCopierEA MT5: CLOSE_RESULT success ticket=", ticket);
   } else {
      string errStr = result.comment != "" ? result.comment
                                           : IntegerToString(result.retcode);
      string msg = CreateSimpleJson(
         "type",        "CLOSE_RESULT",
         "copy_id",     copy_id,
         "success",     "false",
         "close_price", "0",
         "error",       errStr
      );
      SendMsg(msg);
      Print("TradeCopierEA MT5: CLOSE_RESULT failed retcode=", result.retcode,
            " ", result.comment);
   }
}

//+------------------------------------------------------------------+
//| Dispatch a single incoming JSON message                          |
//+------------------------------------------------------------------+
void RouteMessage(string rawMsg) {
   JsonKeyValue pairs[];
   if (!ParseSimpleJson(rawMsg, pairs)) {
      Print("TradeCopierEA MT5: Failed to parse: ", rawMsg);
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
      Print("TradeCopierEA MT5: ACK_REGISTER confirmed path=",
            GetJsonValue(pairs, "terminal_path"));
   }
   else if (type == "HEARTBEAT") {
      // No action needed
   }
   else {
      Print("TradeCopierEA MT5: Unknown message type: ", type);
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
         isConnected       = true;
         lastHeartbeat     = TimeCurrent();
         lastAccountUpdate = TimeCurrent();
         UpdateStatusLabel(true);
         Print("TradeCopierEA MT5: Connected to ", ServerHost, ":", ServerPort);
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
      Print("TradeCopierEA MT5: Connection lost, will retry");
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
   Print("TradeCopierEA MT5 initialised, tracking ", trackedCount, " existing positions");
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
   Print("TradeCopierEA MT5 deinitialised");
}

//+------------------------------------------------------------------+
//| Timer — fires every TimerIntervalMs milliseconds                 |
//| Handles connection loop, heartbeats, and periodic account updates|
//+------------------------------------------------------------------+
void OnTimer() {
   HandleConnection();

   if (!isConnected) return;

   ProcessIncomingMessages();
   CheckPositionChanges();
   MaybeSendAccountUpdate();
   MaybeSendHeartbeat();
}

//+------------------------------------------------------------------+
//| OnTrade — fires instantly on any position change (MT5 only)      |
//| Provides near-zero latency; OnTimer() remains as safety-net.     |
//+------------------------------------------------------------------+
void OnTrade() {
   if (!isConnected) return;
   CheckPositionChanges();
}
