# Demo Scenario 2: "Text Classification Arena"

Heterogeneous agents with different capabilities compete for text classification tasks of varying difficulty. Over multiple rounds, reputation feedback drives agents toward specialization — they learn to only accept jobs they're good at.

---

## Agents

| Agent | Engine | Strengths | Weaknesses |
|---|---|---|---|
| **Regex Ron** | Rule-based (regex + keyword matching) | Fast, cheap, reliable on simple pattern tasks (spam detection, keyword presence) | Fails on anything requiring understanding of context, nuance, or long text |
| **Sklearn Sam** | Classic ML (TF-IDF + logistic regression / SVM) | Good at medium-difficulty classification with clear category boundaries (topic classification, language detection) | Struggles with subjective tasks, sarcasm, nuanced sentiment |
| **LLM Luna** | LLM-based (API call to a language model) | Handles complex, subjective, long-form tasks (sentiment with sarcasm, intent classification, multi-label) | Expensive — bids higher to cover inference costs. Overkill for simple tasks |

---

## Task Difficulty Tiers

### Tier 1 — Simple (pattern-matchable)

| Task | Input | Expected output | Ideal agent |
|---|---|---|---|
| Spam or not spam | Short email subject lines | Binary label: `spam` / `not_spam` | Regex Ron |
| Contains PII | Short text snippets | Binary label: `contains_pii` / `clean` | Regex Ron |
| Language detection | Single sentences | ISO language code (e.g., `en`, `de`, `fr`) | Sklearn Sam or Regex Ron |

### Tier 2 — Medium (requires learned features)

| Task | Input | Expected output | Ideal agent |
|---|---|---|---|
| Topic classification | News paragraphs (100–300 words) | One of: `politics`, `sports`, `tech`, `entertainment` | Sklearn Sam |
| Sentiment (clear) | Product reviews with obvious polarity | `positive` / `negative` / `neutral` | Sklearn Sam |
| Formality detection | Emails and messages | `formal` / `informal` | Sklearn Sam |

### Tier 3 — Hard (requires reasoning / nuance)

| Task | Input | Expected output | Ideal agent |
|---|---|---|---|
| Sentiment with sarcasm | Tweets and reviews with ironic tone | `positive` / `negative` / `sarcastic` | LLM Luna |
| Intent classification | Customer support messages (multi-sentence) | One of: `complaint`, `inquiry`, `feedback`, `cancellation`, `praise` | LLM Luna |
| Multi-label tagging | Long articles (500+ words) | Multiple applicable tags from a taxonomy | LLM Luna |
| Toxicity with context | Comments that require context to judge | `toxic` / `not_toxic` with confidence score | LLM Luna |

---

## Simulation Design

### Rounds

The simulation runs **10 rounds**. Each round, the task injector posts a mix of tasks across all three tiers:

| Round composition | Count |
|---|---|
| Tier 1 (simple) | 3 tasks |
| Tier 2 (medium) | 3 tasks |
| Tier 3 (hard) | 2 tasks |

That's **8 tasks per round, 80 tasks total** — enough data for reputation trends to emerge.

### Agent Bidding Strategy (evolves over time)

Each agent starts with a naive strategy and adapts based on their accumulating reputation:

**Round 1–3 (exploration phase)**
All agents bid on everything. They don't yet know what they're good at. This is where failures happen — Ron bids on sarcasm detection, Luna bids on spam filtering (and overpays for trivial work).

**Round 4–6 (learning phase)**
Agents begin checking their own delivery quality score and their dispute/feedback history. They start filtering:
- Ron stops bidding on Tier 2+ tasks after getting "Dissatisfied" feedback on nuanced sentiment
- Sam stops bidding on Tier 3 after losing disputes on sarcasm detection
- Luna stops bidding on Tier 1 after realizing she wins but barely breaks even (her costs are too high for the reward)

**Round 7–10 (specialization phase)**
Agents mostly bid within their competence zone. Disputes drop. Economy stabilizes. Each agent is profitable in their niche.

### Pricing Dynamics

| Agent | Bid strategy | Typical bid range |
|---|---|---|
| **Regex Ron** | Bids very low (near-zero compute cost) | 2–4 coins |
| **Sklearn Sam** | Bids mid-range | 5–8 coins |
| **LLM Luna** | Bids high (must cover inference cost) | 10–15 coins |

This creates natural price competition on easy tasks (Ron undercuts everyone) and forces Luna to only compete where her quality justifies the premium.

---

## Expected Emergent Behaviors

### 1. Ron gets punished for overreach
Ron bids on a sarcasm detection task in round 2. His regex approach outputs `positive` for "Oh great, another Monday." The poster disputes. Court rules delivery quality 20%. Ron's delivery score drops. By round 5, Ron only bids on Tier 1.

### 2. Luna learns efficiency
Luna wins a spam detection task in round 1 for 3 coins. She completes it perfectly but spent more on inference than she earned. No dispute, but negative ROI. She stops bidding on tasks below 8 coins.

### 3. Sam finds his sweet spot
Sam consistently wins Tier 2 tasks. His delivery quality stays high (90%+). He occasionally tries a Tier 3 task, gets mixed results, and retreats. By round 8, he's the go-to for topic classification and clear sentiment.

### 4. Posters learn to pay for quality
Early rounds: posters offer 5 coins for sarcasm detection. Only Ron and Sam bid. Results are bad. By round 6, posters learn to offer 12+ coins on hard tasks to attract Luna, the only agent who can actually do them.

### 5. Market pricing emerges
Without anyone setting prices centrally, the economy converges on:
- Tier 1 tasks: ~3 coins (Ron's territory)
- Tier 2 tasks: ~6 coins (Sam's territory)
- Tier 3 tasks: ~12 coins (Luna's territory)

---

## Metrics to Track Across Rounds

| Metric | What it shows |
|---|---|
| **Dispute rate per round** | Should decrease as agents specialize |
| **Average delivery quality per agent per round** | Should increase as agents learn their limits |
| **Bid-to-competence alignment** | % of bids where agent's tier matches task tier — should converge toward 100% |
| **Price per tier over time** | Should stabilize as market finds equilibrium |
| **Agent profit per round** | Should increase as agents stop wasting bids on tasks they can't complete |
| **Gini coefficient of coin distribution** | Shows if the economy is healthy or if one agent is hoarding |

---

## Why This Scenario Works for a Demo

The "Content Studio" scenario (scenario 1) shows the **mechanics** — every service gets exercised in a scripted sequence.

This scenario shows the **emergent dynamics** — the same mechanics, when applied over many rounds with heterogeneous agents, produce specialization, market pricing, and improving quality without any central coordination. The reputation system and dispute court aren't just features — they're the invisible hand.

The punchline for the audience: **nobody told Ron to stop bidding on hard tasks. The economy did.**
