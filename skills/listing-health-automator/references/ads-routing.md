# ADS Team Signal Protocol

## Signal types and routing

The Listing Health Automator sends structured signals to the ADS team (Jason + ad reps)
so they can act on campaign decisions without reading every PM update thread manually.

## Signal categories

### PAUSE (act within 1 hour)
Triggered when spending budget on a listing that cannot convert.

| Trigger | Reason |
|---|---|
| PDP Status = OOS (FBA = 0, no active offer) | Zero inventory = zero conversions, pure waste |
| PDP Status = Suppressed (any type) | Listing not visible to customers |
| PDP Status = ADS Suppressed | Campaigns running but Amazon blocking ad delivery |
| PDP Status = Inactive | Listing deactivated by Amazon |

**Signal format:**
```
PAUSE CAMPAIGN — [SKU] ([ASIN])
Reason: [suppression type or OOS]
PM: [name] is working on resolution
Est. resolution: [if known from case]
Monday item: [link]
```

### REVIEW (act within same business day)
Triggered when a listing can still sell but something is degrading performance.

| Trigger | Reason |
|---|---|
| BuyBox lost to FBM | Our ad may be driving traffic that competitor converts |
| BuyBox lost to 3P | Same as FBM — check if we can win BB back |
| Prolonged delivery in 2+ regions | Conversion will suffer — may want to reduce bids |
| Low stock flag | Conversion will suffer when stock gets very low |
| Keyword rank dropped significantly | Organic visibility dropping — may need bid increase |

**Signal format:**
```
REVIEW — [SKU] ([ASIN])
Condition: [BuyBox lost to FBM / prolonged delivery / rank drop]
Recommended: [reduce bids / pause / increase bids — depending on condition]
Current stock: FBA [N] / WH [N]
```

### RESUME (when PM/Compliance clears an issue)
Triggered when a previously paused ASIN is resolved and ready for campaigns.

| Trigger | Condition to resume |
|---|---|
| Suppression resolved | PDP Status returns to Live |
| FBA restocked | FBA stock > threshold (default: 20 units) |
| Regional delivery restored | Stock distributed to all FC regions |

**Signal format:**
```
RESUME READY — [SKU] ([ASIN])
Previously: [what the issue was]
Now: [PDP Status = Live / FBA = N units]
Cleared by: [PM name / Compliance]
```

## Campaign pause logging

When ad rep pauses a campaign based on an automator signal, they should log it
in the Monday item update with this format so the system can track:

```
CAMPAIGN ACTION: [PAUSED / RESUMED / BID ADJUSTED]
Reason: [issue type]
Campaigns affected: [campaign names or "all"]
Action by: [name]
```

This allows the resume signal to fire automatically when the issue resolves,
rather than the ad rep having to remember which campaigns they paused.

## Ad rep assignments (current)

| Brand/Category | ADS Rep |
|---|---|
| Lifepro — Vibration, Bikes, Massagers | Jose Johan Estevez |
| Lifepro — Sauna, Home Gym | Hector Jose Castro Baez |
| Petcove | Yahve Mena |
| Joyberri, Oaktiv, Culvani | Syed Taha Ali / Jose |

Always use the ADS Rep column from the Monday board as the source of truth — assignments change.
