//+------------------------------------------------------------------+
//|                                                   SimpleJson.mqh |
//|                                     Copyright 2025, Kevin Bruton |
//|                                                                  |
//| 24.10.2025 - Initial release                                     |
//+------------------------------------------------------------------+
/*
JSON PARSING & SENDING CAPABILITY:
-----------------------------------
This client now includes built-in simple JSON parsing and creation
for flat key-value pairs (no nesting). Example JSON format:

   {"action":"buy", "symbol":"EURUSD", "price":"1.1234"}

RECEIVING JSON:
The parser provides these functions:
   - ParseSimpleJson(): Parse JSON string into key-value pairs
   - GetJsonValue(): Retrieve value by key name
   - PrintJsonPairs(): Display all parsed pairs (for debugging)

SENDING JSON:
The builder provides these functions:
   - CreateSimpleJson(): Create JSON from up to 10 key-value pairs
   - BuildSimpleJson(): Build JSON from JsonKeyValue array
   - SendJsonMessage(): Send JSON message through socket

Usage examples:
   // Send JSON with CreateSimpleJson (easy method)
   string json = CreateSimpleJson("action", "buy", "symbol", "EURUSD", "price", "1.1234");
   glbClientSocket.Send(json + "\r\n");
   
   // Send JSON with array (flexible method)
   JsonKeyValue pairs[];
   ArrayResize(pairs, 2);
   pairs[0].key = "status"; pairs[0].value = "ok";
   pairs[1].key = "message"; pairs[1].value = "completed";
   SendJsonMessage(glbClientSocket, pairs);

Note: The JSON parser/builder handles simple structures only. It supports
escaped characters in flat string values, but not nested objects or arrays.
*/
#property copyright "Copyright 2025, Kevin Bruton"
#property link      ""
#property strict


// --------------------------------------------------------------------
// Simple JSON parser for flat key-value pairs
// --------------------------------------------------------------------

// Structure to hold JSON key-value pairs
struct JsonKeyValue {
   string key;
   string value;
};

// Convert a hex character code to its numeric value, or -1 if invalid
int JsonHexValue(ushort ch) {
   if (ch >= 48 && ch <= 57)  return (int)(ch - 48);  // 0-9
   if (ch >= 65 && ch <= 70)  return (int)(ch - 55);  // A-F
   if (ch >= 97 && ch <= 102) return (int)(ch - 87);  // a-f
   return -1;
}

// Escape a string so it is valid inside a JSON string literal
string JsonEscape(string str) {
   string result = "";

   for (int i = 0; i < StringLen(str); i++) {
      ushort ch = StringGetCharacter(str, i);

      if (ch == 34) {              // "
         result += "\\\"";
      } else if (ch == 92) {       // backslash
         result += "\\\\";
      } else if (ch == 8) {        // backspace
         result += "\\b";
      } else if (ch == 9) {        // tab
         result += "\\t";
      } else if (ch == 10) {       // line feed
         result += "\\n";
      } else if (ch == 12) {       // form feed
         result += "\\f";
      } else if (ch == 13) {       // carriage return
         result += "\\r";
      } else if (ch < 32) {
         result += StringFormat("\\u%04X", (int)ch);
      } else {
         result += StringSubstr(str, i, 1);
      }
   }

   return result;
}

// Decode JSON escapes from a flat string value
string JsonUnescape(string str) {
   string result = "";

   for (int i = 0; i < StringLen(str); i++) {
      ushort ch = StringGetCharacter(str, i);
      if (ch != 92) { // backslash
         result += StringSubstr(str, i, 1);
         continue;
      }

      if (i + 1 >= StringLen(str)) {
         result += "\\";
         break;
      }

      i++;
      ushort esc = StringGetCharacter(str, i);

      if (esc == 34) {             // "
         result += "\"";
      } else if (esc == 92) {      // backslash
         result += "\\";
      } else if (esc == 47) {      // /
         result += "/";
      } else if (esc == 98) {      // b
         result += ShortToString((ushort)8);
      } else if (esc == 102) {     // f
         result += ShortToString((ushort)12);
      } else if (esc == 110) {     // n
         result += "\n";
      } else if (esc == 114) {     // r
         result += "\r";
      } else if (esc == 116) {     // t
         result += "\t";
      } else if (esc == 117 && i + 4 < StringLen(str)) { // u
         int code = 0;
         bool valid = true;
         for (int j = 1; j <= 4; j++) {
            int hv = JsonHexValue(StringGetCharacter(str, i + j));
            if (hv < 0) {
               valid = false;
               break;
            }
            code = code * 16 + hv;
         }
         if (valid) {
            result += ShortToString((ushort)code);
            i += 4;
         } else {
            result += "\\u";
         }
      } else {
         result += StringSubstr(str, i, 1);
      }
   }

   return result;
}

// Find the closing quote for a JSON string, respecting backslash escapes
int JsonStringEnd(string jsonStr, int startPos) {
   bool escaped = false;
   int len = StringLen(jsonStr);

   for (int pos = startPos; pos < len; pos++) {
      ushort ch = StringGetCharacter(jsonStr, pos);

      if (escaped) {
         escaped = false;
         continue;
      }
      if (ch == 92) { // backslash
         escaped = true;
         continue;
      }
      if (ch == 34) { // "
         return pos;
      }
   }

   return -1;
}

// Parse simple JSON with no nesting (flat key-value pairs only)
bool ParseSimpleJson(string jsonStr, JsonKeyValue &pairs[]) {
   ArrayResize(pairs, 0);
   
   // Remove whitespace
   StringTrimLeft(jsonStr);
   StringTrimRight(jsonStr);
   
   // Check for opening and closing braces
   if (StringLen(jsonStr) < 2) return false;
   if (StringGetCharacter(jsonStr, 0) != '{') return false;
   if (StringGetCharacter(jsonStr, StringLen(jsonStr) - 1) != '}') return false;
   
   // Remove braces
   jsonStr = StringSubstr(jsonStr, 1, StringLen(jsonStr) - 2);
   
   // Manual parsing to handle quoted strings properly
   int pos = 0;
   int len = StringLen(jsonStr);
   
   while (pos < len) {
      // Skip whitespace
      while (pos < len && (StringGetCharacter(jsonStr, pos) == ' ' || 
                           StringGetCharacter(jsonStr, pos) == '\t' ||
                           StringGetCharacter(jsonStr, pos) == '\n' ||
                           StringGetCharacter(jsonStr, pos) == '\r')) {
         pos++;
      }
      
      if (pos >= len) break;
      
      // Find key (should be quoted)
      int keyStart = pos;
      if (StringGetCharacter(jsonStr, pos) == '"') {
         pos++; // skip opening quote
         keyStart = pos;
         int keyEnd = JsonStringEnd(jsonStr, pos);
         if (keyEnd < 0) break;
         
         string key = JsonUnescape(StringSubstr(jsonStr, keyStart, keyEnd - keyStart));
         pos = keyEnd + 1; // skip closing quote
         
         // Skip whitespace and colon
         while (pos < len && (StringGetCharacter(jsonStr, pos) == ' ' || 
                              StringGetCharacter(jsonStr, pos) == '\t' ||
                              StringGetCharacter(jsonStr, pos) == ':')) {
            pos++;
         }
         
         if (pos >= len) break;
         
         // Skip whitespace before value
         while (pos < len && (StringGetCharacter(jsonStr, pos) == ' ' || 
                              StringGetCharacter(jsonStr, pos) == '\t')) {
            pos++;
         }
         
         // Find value
         string value = "";
         if (StringGetCharacter(jsonStr, pos) == '"') {
            // Quoted string value
            pos++; // skip opening quote
            int valueStart = pos;
            int valueEnd = JsonStringEnd(jsonStr, pos);
            if (valueEnd < 0) break;
            value = JsonUnescape(StringSubstr(jsonStr, valueStart, valueEnd - valueStart));
            pos = valueEnd + 1; // skip closing quote
         } else {
            // Unquoted value (number, boolean, null)
            int valueStart = pos;
            while (pos < len && StringGetCharacter(jsonStr, pos) != ',' && 
                   StringGetCharacter(jsonStr, pos) != '}') {
               pos++;
            }
            value = StringSubstr(jsonStr, valueStart, pos - valueStart);
            StringTrimLeft(value);
            StringTrimRight(value);
         }
         
         // Add key-value pair
         int size = ArraySize(pairs);
         ArrayResize(pairs, size + 1);
         pairs[size].key = key;
         pairs[size].value = value;
         
         // Skip comma if present
         while (pos < len && (StringGetCharacter(jsonStr, pos) == ',' || 
                              StringGetCharacter(jsonStr, pos) == ' ' ||
                              StringGetCharacter(jsonStr, pos) == '\t')) {
            pos++;
         }
      } else {
         // Invalid format, skip to next comma
         while (pos < len && StringGetCharacter(jsonStr, pos) != ',') pos++;
         pos++;
      }
   }
   
   return ArraySize(pairs) > 0;
}

// Remove quotes from a string
string RemoveQuotes(string str) {
   StringTrimLeft(str);
   StringTrimRight(str);
   
   int len = StringLen(str);
   if (len < 2) return str;
   
   ushort firstChar = StringGetCharacter(str, 0);
   ushort lastChar = StringGetCharacter(str, len - 1);
   
   if ((firstChar == '"' || firstChar == '\'') && (lastChar == '"' || lastChar == '\'')) {
      return StringSubstr(str, 1, len - 2);
   }
   
   return str;
}

// Get value by key from parsed JSON
string GetJsonValue(JsonKeyValue &pairs[], string key) {
   for (int i = 0; i < ArraySize(pairs); i++) {
      if (pairs[i].key == key) {
         return pairs[i].value;
      }
   }
   return "";
}

// Print all key-value pairs (for debugging)
void PrintJsonPairs(JsonKeyValue &pairs[]) {
   Print("JSON contains ", ArraySize(pairs), " key-value pairs:");
   for (int i = 0; i < ArraySize(pairs); i++) {
      Print("  ", pairs[i].key, " = ", pairs[i].value);
   }
}

// Build JSON string from key-value pairs
string BuildSimpleJson(JsonKeyValue &pairs[]) {
   if (ArraySize(pairs) == 0) return "{}";
   
   string json = "{";
   
   for (int i = 0; i < ArraySize(pairs); i++) {
      if (i > 0) json += ",";
      
      // Add key with quotes
      json += "\"" + JsonEscape(pairs[i].key) + "\":";
      
      // Add value with quotes
      json += "\"" + JsonEscape(pairs[i].value) + "\"";
   }
   
   json += "}";
   return json;
}

// Build JSON string from individual key-value parameters (up to 20 pairs - expanded from 10)
string CreateSimpleJson(
   string key1 = "", string val1 = "",
   string key2 = "", string val2 = "",
   string key3 = "", string val3 = "",
   string key4 = "", string val4 = "",
   string key5 = "", string val5 = "",
   string key6 = "", string val6 = "",
   string key7 = "", string val7 = "",
   string key8 = "", string val8 = "",
   string key9 = "", string val9 = "",
   string key10 = "", string val10 = "",
   string key11 = "", string val11 = "",
   string key12 = "", string val12 = "",
   string key13 = "", string val13 = "",
   string key14 = "", string val14 = "",
   string key15 = "", string val15 = "",
   string key16 = "", string val16 = "",
   string key17 = "", string val17 = "",
   string key18 = "", string val18 = "",
   string key19 = "", string val19 = "",
   string key20 = "", string val20 = ""
) {
   JsonKeyValue pairs[];
   int count = 0;
   
   if (key1 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key1; pairs[count].value = val1; count++; }
   if (key2 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key2; pairs[count].value = val2; count++; }
   if (key3 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key3; pairs[count].value = val3; count++; }
   if (key4 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key4; pairs[count].value = val4; count++; }
   if (key5 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key5; pairs[count].value = val5; count++; }
   if (key6 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key6; pairs[count].value = val6; count++; }
   if (key7 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key7; pairs[count].value = val7; count++; }
   if (key8 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key8; pairs[count].value = val8; count++; }
   if (key9 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key9; pairs[count].value = val9; count++; }
   if (key10 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key10; pairs[count].value = val10; count++; }
   if (key11 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key11; pairs[count].value = val11; count++; }
   if (key12 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key12; pairs[count].value = val12; count++; }
   if (key13 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key13; pairs[count].value = val13; count++; }
   if (key14 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key14; pairs[count].value = val14; count++; }
   if (key15 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key15; pairs[count].value = val15; count++; }
   if (key16 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key16; pairs[count].value = val16; count++; }
   if (key17 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key17; pairs[count].value = val17; count++; }
   if (key18 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key18; pairs[count].value = val18; count++; }
   if (key19 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key19; pairs[count].value = val19; count++; }
   if (key20 != "") { ArrayResize(pairs, count + 1); pairs[count].key = key20; pairs[count].value = val20; count++; }
   
   return BuildSimpleJson(pairs);
}
