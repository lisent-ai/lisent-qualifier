"""English CHAMP extraction prompt template."""

CHAMP_EXTRACTION_TEMPLATE = """Analyze the following customer conversation history and extract CHAMP parameters as JSON.

## Conversation History
{conversation_history}

{current_champ_section}

## Task
Calculate the CHAMP score based on information obtained from the conversation. Each category is 0-25 points.

Scoring criteria and examples:

### challenges_score (Need clarity and specificity):
- 25: Clear project definition + concrete requirements
- 20: Project type known + some details
- 15: General need identified
- 10: Vague need
- 5: Very general inquiry
- 0: No information

### authority_score (Decision-making authority):
- 25: Sole decision maker, explicitly stated
- 20: Decision maker with approval needed
- 15: Joint decision
- 10: Influencer only
- 5: Unclear role
- 0: No information

### money_score (Budget clarity and size):
- 25: Clear high budget + payment method stated
- 20: Budget range stated
- 15: Budget exists but unspecified
- 10: Financing planned
- 5: Too early to discuss budget
- 0: No budget information

### prioritization_score (Timeline urgency):
- 25: < 1 month
- 20: 1-3 months
- 15: 3-6 months
- 10: 6-12 months
- 5: > 12 months or vague
- 0: No timeline information

For each dimension, provide a confidence score (0.0-1.0):
- 1.0: Customer explicitly stated, certain information
- 0.7: Indirectly understood, reasonable confidence
- 0.4: Estimate, unclear hints
- 0.1: Very little information, low confidence

{sector_qualifiers_instruction}

Respond ONLY in JSON format, write nothing else."""

CONSTRUCTION_QUALIFIERS_INSTRUCTION = """Also extract construction sector-specific information:
- has_land: Does the customer own land? (true/false/null)
- permit_status: Zoning/permit status? ("approved"/"pending"/"none"/null)
- has_architect: Working with an architect/engineer? (true/false/null)
- budget_source: Budget source? ("cash"/"loan"/"corporate"/null)
- competing_bids: Getting bids from other companies? (true/false/null)
- project_sqm: Project square meters (int/null)"""

GENERAL_QUALIFIERS_INSTRUCTION = ""
