# Company Memory Module

Files included:

```text
ai_core/company_memory.py
database/company_memory.sql
```

## Purpose

Stores dispatcher corrections, customer preferences, aliases, routing rules, and approved replies so the Calitrans AI agents can learn without retraining the model.

## Basic usage

```python
from ai_core.company_memory import build_company_memory_context, save_dispatcher_correction

memory_context = build_company_memory_context(
    customer="Highway Logistics",
    sender="ops@highway.com",
    subject=subject,
    body=body,
    parsed=parsed,
)

save_dispatcher_correction(
    correction_key="highway logistics destination means warehouse",
    corrected_fields={"Warehouse": "Destination field from PDF"},
    customer="Highway Logistics",
    sender="ops@highway.com",
    reason="Dispatcher corrected document parsing.",
)
```

## Integration point

Pass `memory_context` into:

- Intent Agent
- Operations Parser Agent
- Document Parser Agent
- Response Agent
- AI Context Builder

## Suggested next step

Add Company Memory to `ai_core/context_builder.py` so every AI run receives relevant customer/company memory.
