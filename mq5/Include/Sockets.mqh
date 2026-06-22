//+------------------------------------------------------------------+
//|                                       TradeCopierSocketMT5.mqh   |
//|                                     Copyright 2025, Kevin Bruton |
//|                                                                  |
//| MT5-native socket wrapper exposing the ClientSocket interface    |
//| used by TradeCopierEA.mq5. This avoids the legacy ws2_32.dll      |
//| import layer in Sockets.mqh, which is fragile on newer MT5 builds.|
//+------------------------------------------------------------------+
#property strict

class ClientSocket
{
   private:
      int    mSocket;
      bool   mConnected;
      int    mLastSocketError;
      string mPendingReceiveData;

      void Init(string host, ushort port);
      void Close();

   public:
      ClientSocket(ushort localport);
      ClientSocket(string HostnameOrIPAddress, ushort port);
      ~ClientSocket();

      bool Send(string strMsg);
      string Receive(string MessageSeparator = "");

      bool IsSocketConnected();
      int GetLastSocketError() { return mLastSocketError; }
      ulong GetSocketHandle() { return (ulong)mSocket; }

      int ReceiveBufferSize;
      int SendBufferSize;
};

//+------------------------------------------------------------------+
//| Connect to 127.0.0.1                                             |
//+------------------------------------------------------------------+
ClientSocket::ClientSocket(ushort localport)
{
   Init("127.0.0.1", localport);
}

//+------------------------------------------------------------------+
//| Connect to a hostname or IP address                              |
//+------------------------------------------------------------------+
ClientSocket::ClientSocket(string HostnameOrIPAddress, ushort port)
{
   Init(HostnameOrIPAddress, port);
}

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
ClientSocket::~ClientSocket()
{
   Close();
}

//+------------------------------------------------------------------+
//| Initialise and connect the native MT5 socket                     |
//+------------------------------------------------------------------+
void ClientSocket::Init(string host, ushort port)
{
   ReceiveBufferSize   = 10000;
   SendBufferSize      = 999999999;
   mSocket             = INVALID_HANDLE;
   mConnected          = false;
   mLastSocketError    = 0;
   mPendingReceiveData = "";

   ResetLastError();
   mSocket = SocketCreate();
   if (mSocket == INVALID_HANDLE) {
      mLastSocketError = GetLastError();
      Print("TradeCopierSocketMT5: SocketCreate failed, error=", mLastSocketError);
      return;
   }

   ResetLastError();
   if (!SocketConnect(mSocket, host, (uint)port, 1000)) {
      mLastSocketError = GetLastError();
      Print("TradeCopierSocketMT5: SocketConnect failed to ", host, ":", port,
            ", error=", mLastSocketError);
      if (mLastSocketError == 4014) {
         long programType = MQLInfoInteger(MQL_PROGRAM_TYPE);
         Print("TradeCopierSocketMT5: error 4014 means socket calls are not allowed "
               "in this MT5 context. MQL_PROGRAM_TYPE=", programType,
               " (PROGRAM_EXPERT=", PROGRAM_EXPERT, ", PROGRAM_SCRIPT=", PROGRAM_SCRIPT,
               ", PROGRAM_INDICATOR=", PROGRAM_INDICATOR, ").");
         Print("TradeCopierSocketMT5: if program type is Expert, check the MT5 allow-list: "
               "Tools > Options > Expert Advisors > Allow WebRequest for listed URL. "
               "Add the exact socket host ", host,
               " (and if MT5 rejects bare hosts, also try http://", host, ").");
      } else if (mLastSocketError == 5272) {
         Print("TradeCopierSocketMT5: error 5272 means MT5 could not connect. "
               "Confirm the Python server is started and listening on ", host, ":", port, ".");
      }
      Close();
      return;
   }

   mConnected = true;
}

//+------------------------------------------------------------------+
//| Close and reset the native socket                                |
//+------------------------------------------------------------------+
void ClientSocket::Close()
{
   if (mSocket != INVALID_HANDLE) {
      SocketClose(mSocket);
      mSocket = INVALID_HANDLE;
   }
   mConnected = false;
}

//+------------------------------------------------------------------+
//| Return true while the native socket remains connected            |
//+------------------------------------------------------------------+
bool ClientSocket::IsSocketConnected()
{
   if (!mConnected || mSocket == INVALID_HANDLE) return false;

   ResetLastError();
   if (!SocketIsConnected(mSocket)) {
      mLastSocketError = GetLastError();
      mConnected = false;
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Send a string over the socket                                    |
//+------------------------------------------------------------------+
bool ClientSocket::Send(string strMsg)
{
   if (!IsSocketConnected()) return false;

   uchar bytes[];
   int bytesToSend = StringToCharArray(strMsg, bytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   if (bytesToSend <= 0) return true;

   int sentTotal = 0;
   while (sentTotal < bytesToSend) {
      int chunkSize = bytesToSend - sentTotal;
      if (chunkSize > SendBufferSize) chunkSize = SendBufferSize;

      uchar chunk[];
      ArrayResize(chunk, chunkSize);
      ArrayCopy(chunk, bytes, 0, sentTotal, chunkSize);

      ResetLastError();
      int sentNow = SocketSend(mSocket, chunk, (uint)chunkSize);
      if (sentNow <= 0) {
         mLastSocketError = GetLastError();
         mConnected = false;
         Print("TradeCopierSocketMT5: SocketSend failed, error=", mLastSocketError);
         return false;
      }

      sentTotal += sentNow;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Receive pending data, optionally split by message separator      |
//+------------------------------------------------------------------+
string ClientSocket::Receive(string MessageSeparator)
{
   if (!IsSocketConnected()) return "";

   uint readable = SocketIsReadable(mSocket);
   while (readable > 0) {
      uint bytesRequested = readable;
      if (ReceiveBufferSize > 0 && bytesRequested > (uint)ReceiveBufferSize)
         bytesRequested = (uint)ReceiveBufferSize;

      uchar buffer[];
      ArrayResize(buffer, (int)bytesRequested);

      ResetLastError();
      int bytesRead = SocketRead(mSocket, buffer, bytesRequested, 0);
      if (bytesRead > 0) {
         StringAdd(mPendingReceiveData, CharArrayToString(buffer, 0, bytesRead, CP_UTF8));
      } else if (bytesRead < 0) {
         mLastSocketError = GetLastError();
         mConnected = false;
         Print("TradeCopierSocketMT5: SocketRead failed, error=", mLastSocketError);
         break;
      } else {
         break;
      }

      readable = SocketIsReadable(mSocket);
   }

   if (mPendingReceiveData == "") return "";

   if (MessageSeparator == "") {
      string allData = mPendingReceiveData;
      mPendingReceiveData = "";
      return allData;
   }

   int idx = StringFind(mPendingReceiveData, MessageSeparator);
   while (idx == 0) {
      mPendingReceiveData = StringSubstr(mPendingReceiveData, StringLen(MessageSeparator));
      idx = StringFind(mPendingReceiveData, MessageSeparator);
   }

   if (idx < 0) return "";

   string message = StringSubstr(mPendingReceiveData, 0, idx);
   mPendingReceiveData = StringSubstr(mPendingReceiveData, idx + StringLen(MessageSeparator));
   return message;
}
