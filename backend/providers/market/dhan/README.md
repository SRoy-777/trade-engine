# Dhan Live Market Data Feed Provider

A high-performance, low-latency market data feed provider designed to consume live tick feeds from the Dhan API using modern asynchronous Python.

## Features
- **Binary Stream Decoding**: Uses Python's native `struct` library to decode binary feed packets directly into Pydantic models.
- **Heartbeat Audits**: Native ping-pong frame validation to detect silent network failures.
- **Robust Reconnection**: Automated socket reconnection using exponential backoff.
- **Structured Logging**: Generates JSON records with automated credential scrubbing.

---

## Configuration

Credentials and endpoint variables are loaded from the environment or a `.env` file located in the `backend/` root directory:

```env
CLIENT_ID=3eb8e412
ACCESS_TOKEN=9ac2db54-62cc-4a8a-b9b2-34f79f4810bc
DHAN_API_KEY=3eb8e412
DHAN_API_SECRET=9ac2db54-62cc-4a8a-b9b2-34f79f4810bc
WS_URL=wss://api-feed.dhan.co
LOG_LEVEL=INFO
```

---

## Authentication

WebSocket connections are authenticated using DhanHQ V2 query parameters appended directly to the URL structure:
`wss://api-feed.dhan.co?version=2&token=<ACCESS_TOKEN>&clientId=<CLIENT_ID>&authType=2`

Format validation is performed locally before connections are attempted:
- Client ID presence verification.
- Personal access token presence verification.

---

## Running the Connection Test

You can run the connection verification independently:

```bash
# From the backend directory
python providers/market/dhan/test_connection.py
```

### Example Test Output:
```text
Connecting...
Authentication Successful
Connected
Subscribed : INFY
LTP : 1620.50
Volume : 24391
Timestamp : 2026-07-05 14:58:30
Packet Count : 1
LTP : 1620.65
...
```

---

## Known Limitations & Design Constraints
1. **No Order Placement**: This component is strictly designed for ingestion/stream access; order placement or account management functions are not implemented.
2. **Batch Subscriptions**: Standard subscriptions must be sent in batches of at most 100 instruments per JSON payload.
3. **No Historical Fetching**: Real-time ticker operations only; historical data requests are handled via Dhan's REST historical candles endpoint.
