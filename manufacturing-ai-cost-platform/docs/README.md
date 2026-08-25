# Manufacturing AI Cost Intelligence — Source-of-Truth Docs

This folder contains the architectural source-of-truth documents for the hackathon implementation.

Documents:
- ARCHITECTURE.md
- API_CONTRACT.yaml
- DATABASE_SCHEMA.md
- AI_WORKFLOWS.md
- SECURITY.md

Database choice:
- SQLite for the hackathon MVP.
- SQLAlchemy repositories/services should keep the design migration-friendly for a future PostgreSQL deployment.

LLM Gateway:
- GenAILab
- https://genailab.tcs.in/v1
- OpenAI-compatible AsyncOpenAI client
- Multimodal support
