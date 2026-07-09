# LLM and web search calls go through a provider-agnostic interface

Pain Point classification, Opportunity clustering, brief writing, and the competitor check all depend on an LLM, and the competitor check additionally depends on web search. Rather than calling one vendor's SDK directly throughout the codebase, these calls go through a thin internal interface (e.g. a `generate(...)` / `search(...)` function with swappable adapters) so the underlying model or search backend can be changed via configuration, not a rewrite.

v1 ships with Claude (Anthropic API) as the default LLM adapter and Claude's built-in web search tool as the default search adapter, since the user is already fluent in this vendor. Other adapters (different LLM providers, a dedicated search API) can be added later without touching call sites.
