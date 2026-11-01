# How good is the bot?

Everything here comes from `tools/evaluate.py`, measured rather than estimated:

1. **Simulated matches.** 90,000 hands against scripted opponents: a calling station, a nit, a maniac and a solid tight-aggressive player (TAG), heads-up and at a mixed 6-max table. Each card sequence is replayed for three hero types:
   - the bot
   - the bot with its opponent memory wiped every hand
   - a simple rule-based TAG baseline
2. **Frequency tests** against what strong players and solvers typically do.
3. **Adaptation tests**: the same spots against different opponent profiles.
4. **Hand reading**: the bot's equity estimate compared with the true equity against the opponent's actual cards, using 2,300 decisions.

"Before" is the first version. "After" is the current engine.

## Report card

| Part of the game | Before | After | What changed |
|---|---|---|---|
| Maths engine | A | A | — |
| Opening ranges, 6-max | A− | A− | UTG 18%, HJ 21%, CO 28%, BTN 45%, SB 42% |
| Heads-up pre-flop | D | B+ | Button opens 75% (was ~42%); big blind defends 62% (a little tight) |
| Defending vs opens | C+ | A− | BTN vs UTG: 3-bet 5% / call 9% (was 19% / 0%) |
| 3-bet pots | C | B+ | Facing a 3-bet: fold 37%, call 56%, 4-bet 8% at normal size (was 64% fold, 11% shove) |
| Aggression / c-betting | C+ | B | C-bet 47–56% (was 40%); still slightly below the 55–65% norm |
| Value betting | A− | A | Bets ~97–100% of strong rivers; thin value adapts to the opponent |
| Bluffing | C | B− | Share of river bets that are bluffs vs the TAG: 5% → 13% (theory: 25–33%); still never bluffs stations |
| Defending vs bets | B− | B+ | vs ⅓-pot bet: fold 31%, raise 9% (was 22% / 27%); vs ¾-pot: fold 52%, raise 12% |
| Hand reading | B | B+ | Error 0.228 → 0.215 (vs the nit 0.214 → 0.164); still about 8 points pessimistic on the river |
| Observing opponents | A− | A | All four types labeled correctly heads-up by hand 25 (maniac and TAG were mislabeled before) |
| Adjusting to opponents | A | A | Steals 54% vs nit blinds, 36% vs loose ones; calls down maniacs, folds to nits |
| **Overall** | **B** | **B+** | |

## Match results (bb/100, ±95% confidence)

| Opponent | Bot before → after | Edge over rule-based baseline | Gain from opponent memory |
|---|---|---|---|
| Calling station | +256 → **+434** ±55 | +192 → **+370** ±44 | +60 → **+229** ±37 |
| Nit | +57 → **+60** ±24 | +64 → **+67** ±26 | +29 → −1 ±22 (not significant) |
| Maniac | +305 → **+391** ±76 | +87 → **+173** ±61 | +208 → **+237** ±58 |
| Solid TAG | +77 → **+96** ±36 | +84 → **+103** ±34 | +23 → +1 ±22 (not significant) |
| 6-max mixed table | +362 → **+422** ±87 | +107 → **+168** ±85 | +121 → **+182** ±65 |

The scripted opponents are far easier to beat than real players, so read these numbers as a comparison between the bot and the baseline, not as an expected win rate. Memory makes a big difference against stations, maniacs and mixed tables. Against nits and solid players, the default assumptions are already close enough.

## Key frequencies vs. typical strong play

| Spot | Before | After | Typical |
|---|---|---|---|
| Button vs UTG open: 3-bet / call | 19% / 0% | 5% / 9% | 5–7% / 8–12% |
| Big blind vs button open: defend / 3-bet | 55% / 12% | 55% / 9% | 55–70% / 11–15% |
| Small blind vs button open: 3-bet | 17% | 12% | 12–16% |
| CO opener facing button 3-bet: fold / 4-bet | 64% / 11% shove | 37% / 8% | 40–55% / 8–12% |
| Heads-up button open | 42% | 75% | ~80% |
| C-bet, button vs big blind | 40% | 47% | 55–65% |
| Big blind vs ⅓-pot c-bet: fold / check-raise | 22% / 27% | 31% / 9% | ≤25–30% / 8–15% |
| Big blind vs ¾-pot c-bet: fold / check-raise | 45% / 19% | 52% / 12% | 35–45% / 8–12% |

## What was fixed

- Opening widths, player priors and player labels now scale with the number of players (heads-up tables play many more hands).
- The 3-bet range model includes suited bluffs and widens against late-position opens. Early-position openers fold less to 3-bets.
- Facing a 3-bet, the bot 4-bets to a normal size instead of shoving 100 big blinds.
- Fold probabilities follow the bet size (anchored to the bettor's break-even fold rate), and the re-raise risk matches real check-raise frequencies.
- Weak hands realise less of their equity and strong hands more. Pre-flop calls with pairs and suited hands get an implied-odds bonus.
- Range reading: frequent c-bettors keep air in their range, and checking to the pre-flop raiser no longer counts as weakness.
- Small safety margins before choosing a pre-flop bluff re-raise or raising a bet. These removed most check-raises that won only by a hair.
- Isolation raises against loose limpers use about the normal opening range.

## Known remaining gaps

- C-bets are a little below the usual frequency, and bluffs against balanced players are still rarer than theory suggests.
- Equity estimates on the river are still slightly pessimistic, so the bot folds a bit too often to river bets from very aggressive players.
- Opponent models start from population averages; reads need roughly 25–100 hands to settle.
