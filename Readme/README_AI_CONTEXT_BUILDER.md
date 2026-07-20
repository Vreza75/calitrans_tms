# AI Context Builder Module

## File included

- `ai_core/context_builder.py`

## Purpose

Builds one shared context package for CaliTrans AI agents:

- Email metadata
- Parsed fields
- Prior AI results
- Conversation timeline
- Attachments
- Case context
- Matched load context
- Customer memory placeholder
- Warnings

This should feed the AI Router and downstream agents so each agent does not independently fetch data or call the LLM.

## Integration

1. Copy `ai_core/context_builder.py` into your project.
2. Make sure these modules exist or are being built:
   - `repositories/inbox_repo.py`
   - `repositories/case_repo.py`
   - `repositories/load_repo.py`
3. Use in Operations Inbox:

```python
from ai_core.context_builder import build_ai_context_from_record

context = build_ai_context_from_record(
    record=record.to_dict() if hasattr(record, "to_dict") else dict(record),
    parsed=parsed,
    body=body,
    load_candidates=load_match_candidates,
    customer_memory={},
)
```

## Future improvement

Later connect customer memory:

```python
customer_memory = load_customer_memory(customer_name, sender)
```

Then pass that into the builder.
