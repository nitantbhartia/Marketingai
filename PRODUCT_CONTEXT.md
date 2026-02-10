# PRODUCT_CONTEXT.md — ClaimCoach Agent Reference

# Last updated: February 2026

# This file is loaded by EVERY agent. All content claims must be validated against this document.

-----

## Product Overview

**Name:** ClaimCoach
**URL:** claimcoach.app
**Tagline:** See the line items they're hoping you'll miss.
**What it is:** An AI-powered tool that analyzes auto insurance total loss settlement offers and identifies commonly missed or undervalued line items so consumers can negotiate a higher payout.
**Target user:** Car owners who have received a total loss settlement offer from their insurance company and suspect it's too low.

-----

## What ClaimCoach DOES (Approved Claims)

- Analyzes auto insurance total loss settlement offers
- Identifies commonly missed or undervalued line items in settlement offers
- Shows users specific line items with typical dollar ranges they may be owed
- Provides state-specific context about insurance regulations and consumer rights
- Helps users understand what a fair settlement should include
- Generates an analysis report users can reference when talking to their adjuster
- Takes approximately 5 minutes to complete
- Works by having the user enter their settlement offer amount and vehicle details

-----

## What ClaimCoach Does NOT Do (Hard Restrictions — Agents MUST NOT Claim These)

- ❌ Does NOT negotiate with insurance companies on the user's behalf
- ❌ Does NOT file claims, disputes, or appeals for the user
- ❌ Does NOT provide legal advice (always recommend consulting an attorney for complex situations)
- ❌ Does NOT guarantee any specific dollar recovery amount
- ❌ Does NOT replace a public adjuster, attorney, or licensed appraiser
- ❌ Does NOT communicate with adjusters or insurance companies
- ❌ Does NOT analyze health insurance, homeowner's, renters, or non-auto claims
- ❌ Does NOT access insurance company systems, databases, or proprietary tools like CCC ONE
- ❌ Does NOT provide binding valuations or legally enforceable appraisals
- ❌ Does NOT generate dispute letters (FUTURE FEATURE — do not reference)
- ❌ Does NOT accept document/PDF uploads for AI parsing (FUTURE FEATURE — do not reference)
- ❌ Does NOT pull comparable vehicle listings automatically (FUTURE FEATURE — do not reference)
- ❌ Does NOT have a chat assistant for claim questions (FUTURE FEATURE — do not reference)
- ❌ Does NOT have status tracking or notifications (FUTURE FEATURE — do not reference)

-----

## Common Line Items ClaimCoach Identifies

These are the core value propositions — the items insurance adjusters commonly omit or undervalue:

|Line Item                             |Typical Range  |Notes                                                                     |
|--------------------------------------|---------------|--------------------------------------------------------------------------|
|Sales tax on replacement vehicle      |$800–$3,000+   |Required in many states (CA, FL, TX, etc.) — often the biggest single miss|
|Title, registration, and transfer fees|$200–$500      |One-time fees to get a replacement vehicle legally registered             |
|Comparable vehicle adjustments        |$500–$2,000    |Mileage, condition, trim level, and option adjustments often skew low     |
|Dealer fees and documentation charges |$300–$800      |Dealer doc fees, destination charges if buying replacement                |
|Aftermarket modifications and upgrades|Varies widely  |Wheels, audio systems, tint, performance parts — often ignored entirely   |
|Loss of use / rental car gap          |$200–$1,500    |Compensation for days without a vehicle beyond rental coverage period     |
|State-specific items                  |Varies by state|Diminished value (GA, NC, KS), emissions testing fees, inspection fees    |

-----

## Approved Marketing Language

Use these phrases freely in content:

- "See the line items they're hoping you'll miss"
- "Most total loss offers are missing line items you're entitled to"
- "ClaimCoach helps you understand what your offer should include"
- "Your insurance company's first offer is a negotiation tactic"
- "The deck is stacked against you — adjusters handle 500+ claims per year. This might be your first."
- "Most people are owed $1,500–$4,000+ more than their first offer"
- "Get your analysis in under 5 minutes"
- "No credit card required" (if applicable at time of publishing)
- "ClaimCoach gives you the information a public adjuster would — in minutes, not weeks"

-----

## Language to NEVER Use

These phrases are either legally risky, inaccurate, or overpromising:

- ❌ "ClaimCoach will get you more money" (implies guarantee)
- ❌ "You are legally entitled to…" (legal advice)
- ❌ "The law requires your insurer to…" (without citing specific state statute)
- ❌ "ClaimCoach negotiates for you" (it doesn't)
- ❌ "Guaranteed results" / "100% success rate"
- ❌ "Average user recovers $X,XXX" (unless backed by real, verified user data)
- ❌ "Our AI reviews your policy" (doesn't do this yet)
- ❌ "Upload your settlement letter" (doesn't do this yet)
- ❌ "ClaimCoach generates your dispute letter" (doesn't do this yet)
- ❌ "Sue your insurance company" / "Take legal action" (not legal advice)
- ❌ Any specific dollar amount as a promise (ranges are OK, promises are not)

-----

## Competitive Positioning

|Competitor      |Their Model                                     |ClaimCoach Advantage                                           |
|----------------|------------------------------------------------|---------------------------------------------------------------|
|Public adjusters|Take 5–15% of settlement, weeks-long process    |Free/low-cost, 5-minute analysis, no commission                |
|Attorneys       |Expensive, overkill for most total loss disputes|Right-sized for the problem — most disputes don't need a lawyer|
|Mighty.com      |Free, broad claim types, lawyer referral model  |Sharper and faster for total loss specifically                 |
|Claims.Coach    |Paid appraisal service ($350+), manual process  |AI-powered, instant, fraction of the cost                      |
|DIY (Google)    |Free but fragmented, no state-specific guidance |Consolidated, personalized, actionable                         |

-----

## Tone & Voice Guidelines

- **Authoritative but empathetic** — we understand how stressful this is
- **Adversarial toward insurance companies**, NOT toward the reader
- **Direct and actionable** — tell people what to do, not just what to know
- **Use "you" and "your"** — speak directly to someone going through this
- **Never condescending** — these are smart people in an unfamiliar situation
- **Urgent but not panicky** — there are real deadlines, but we're here to help
- **Avoid jargon** unless defining it immediately (e.g., "diminished value — the drop in your car's resale value after an accident, even after repairs")
- **Specific over vague** — use dollar ranges, state names, insurer names. Specificity builds trust.
- **NOT a tech startup voice** — no "disrupt," "leverage," "revolutionize." This audience is stressed, not shopping for SaaS.

-----

## Technical Details for Agents

- **Domain:** claimcoach.app
- **Blog URL:** claimcoach.app/blog/ (if set up)
- **Current pricing model:** [FILL IN — free tier, paid analysis, etc.]
- **States covered:** All 50 states (with varying depth of state-specific data)
- **Primary traffic sources:** Organic search (target), Reddit, social media
- **Target keywords:** See Scout agent spec for full keyword strategy
