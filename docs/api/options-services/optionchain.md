# OptionChain

Get the complete option chain for a given underlying and expiry, including live quotes for all strikes.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/optionchain
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "strike_count": 3
}
```

## Sample API Response

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "underlying_ltp": 24231.30,
  "expiry_date": "28APR26",
  "atm_strike": 24250.0,
  "chain": [
    {
      "strike": 24100.0,
      "ce": {
        "symbol": "NIFTY28APR2624100CE",
        "label": "ITM3",
        "ltp": 385.20,
        "bid": 384.50,
        "ask": 385.80,
        "open": 370.00,
        "high": 410.30,
        "low": 352.45,
        "prev_close": 372.15,
        "volume": 1456200,
        "oi": 987650,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624100PE",
        "label": "OTM3",
        "ltp": 198.50,
        "bid": 197.80,
        "ask": 199.20,
        "open": 215.00,
        "high": 230.40,
        "low": 185.60,
        "prev_close": 218.90,
        "volume": 1890400,
        "oi": 1123400,
        "lotsize": 65,
        "tick_size": 0.05
      }
    },
    {
      "strike": 24150.0,
      "ce": {
        "symbol": "NIFTY28APR2624150CE",
        "label": "ITM2",
        "ltp": 352.60,
        "bid": 351.90,
        "ask": 353.30,
        "open": 340.00,
        "high": 378.50,
        "low": 320.80,
        "prev_close": 342.70,
        "volume": 1678900,
        "oi": 876500,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624150PE",
        "label": "OTM2",
        "ltp": 218.40,
        "bid": 217.60,
        "ask": 219.10,
        "open": 232.50,
        "high": 250.80,
        "low": 205.30,
        "prev_close": 235.60,
        "volume": 2012300,
        "oi": 1245600,
        "lotsize": 65,
        "tick_size": 0.05
      }
    },
    {
      "strike": 24200.0,
      "ce": {
        "symbol": "NIFTY28APR2624200CE",
        "label": "ITM1",
        "ltp": 322.80,
        "bid": 322.10,
        "ask": 323.50,
        "open": 310.00,
        "high": 350.20,
        "low": 292.50,
        "prev_close": 315.40,
        "volume": 2345600,
        "oi": 1098700,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624200PE",
        "label": "OTM1",
        "ltp": 245.60,
        "bid": 244.80,
        "ask": 246.30,
        "open": 258.00,
        "high": 275.40,
        "low": 230.10,
        "prev_close": 260.50,
        "volume": 2567800,
        "oi": 1456300,
        "lotsize": 65,
        "tick_size": 0.05
      }
    },
    {
      "strike": 24250.0,
      "ce": {
        "symbol": "NIFTY28APR2624250CE",
        "label": "ATM",
        "ltp": 311.85,
        "bid": 311.10,
        "ask": 312.50,
        "open": 288.00,
        "high": 340.50,
        "low": 267.15,
        "prev_close": 297.40,
        "volume": 3254650,
        "oi": 1245800,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624250PE",
        "label": "ATM",
        "ltp": 285.20,
        "bid": 284.50,
        "ask": 285.90,
        "open": 310.00,
        "high": 335.60,
        "low": 262.45,
        "prev_close": 305.80,
        "volume": 2876400,
        "oi": 1123500,
        "lotsize": 65,
        "tick_size": 0.05
      }
    },
    {
      "strike": 24300.0,
      "ce": {
        "symbol": "NIFTY28APR2624300CE",
        "label": "OTM1",
        "ltp": 278.50,
        "bid": 277.80,
        "ask": 279.20,
        "open": 265.00,
        "high": 305.30,
        "low": 245.60,
        "prev_close": 272.80,
        "volume": 2890100,
        "oi": 1345600,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624300PE",
        "label": "ITM1",
        "ltp": 310.40,
        "bid": 309.60,
        "ask": 311.10,
        "open": 325.00,
        "high": 348.20,
        "low": 295.80,
        "prev_close": 328.50,
        "volume": 2123400,
        "oi": 1098700,
        "lotsize": 65,
        "tick_size": 0.05
      }
    },
    {
      "strike": 24350.0,
      "ce": {
        "symbol": "NIFTY28APR2624350CE",
        "label": "OTM2",
        "ltp": 248.30,
        "bid": 247.50,
        "ask": 249.00,
        "open": 238.00,
        "high": 275.40,
        "low": 220.80,
        "prev_close": 245.60,
        "volume": 2456700,
        "oi": 1567800,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624350PE",
        "label": "ITM2",
        "ltp": 338.90,
        "bid": 338.10,
        "ask": 339.60,
        "open": 352.00,
        "high": 372.50,
        "low": 318.40,
        "prev_close": 355.20,
        "volume": 1876500,
        "oi": 987600,
        "lotsize": 65,
        "tick_size": 0.05
      }
    },
    {
      "strike": 24400.0,
      "ce": {
        "symbol": "NIFTY28APR2624400CE",
        "label": "OTM3",
        "ltp": 220.10,
        "bid": 219.30,
        "ask": 220.80,
        "open": 212.00,
        "high": 248.60,
        "low": 198.50,
        "prev_close": 220.40,
        "volume": 2123400,
        "oi": 1678900,
        "lotsize": 65,
        "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28APR2624400PE",
        "label": "ITM3",
        "ltp": 368.50,
        "bid": 367.70,
        "ask": 369.20,
        "open": 380.00,
        "high": 398.40,
        "low": 345.60,
        "prev_close": 382.80,
        "volume": 1567800,
        "oi": 876500,
        "lotsize": 65,
        "tick_size": 0.05
      }
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying symbol (NIFTY, BANKNIFTY, SENSEX) | Mandatory | - |
| exchange | Exchange: NSE_INDEX, BSE_INDEX | Mandatory | - |
| expiry_date | Expiry date in DDMMMYY format | Mandatory | - |
| strike_count | Number of strikes above and below ATM | Optional | All strikes |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| underlying | string | Underlying symbol |
| underlying_ltp | number | Current underlying price |
| expiry_date | string | Expiry date |
| atm_strike | number | At-the-money strike price |
| chain | array | Array of strike data |

### Chain Array Fields

| Field | Type | Description |
|-------|------|-------------|
| strike | number | Strike price |
| ce | object | Call option data |
| pe | object | Put option data |

### Option Data Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Option symbol |
| label | string | ATM, ITM1, ITM2..., OTM1, OTM2... |
| ltp | number | Last traded price |
| bid | number | Best bid price |
| ask | number | Best ask price |
| open | number | Day's open |
| high | number | Day's high |
| low | number | Day's low |
| prev_close | number | Previous close |
| volume | number | Trading volume |
| oi | number | Open interest |
| lotsize | number | Lot size |
| tick_size | number | Tick size |

## Notes

- With **strike_count** of 3, returns 7 strikes (3 below ATM + ATM + 3 above ATM)
- Without **strike_count**, returns the **entire option chain** for the expiry
- The **label** field indicates whether the option is ATM, ITM, or OTM
- For CE options: strikes below ATM are ITM, above are OTM
- For PE options: strikes above ATM are ITM, below are OTM
- Use this for **options analysis** and **strategy selection**

## Use Cases

- **Option analysis**: View premiums across strikes
- **Strategy selection**: Find suitable strikes for spreads/strangles
- **Volatility analysis**: Compare premiums at different strikes

---

**Back to**: [API Documentation](../README.md)
