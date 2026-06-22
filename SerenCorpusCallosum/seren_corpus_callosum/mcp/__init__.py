"""
seren_corpus_callosum.mcp
═════════════════════════

Optional MCP server surface for SerenCorpusCallosum. Only meaningful when the
[mcp] extra is installed (`pip install seren-corpus-callosum[mcp]`); without
those deps this subpackage's modules fail to import and app.py's mount-attempt
silently no-ops, leaving the callosum in pure-HTTP mode.

This is the surface a connected model reaches the WHOLE brain through: one
`search` call fans every configured store (left + right + however many more),
RRF-merges the results, and hands back a single ranked list with provenance.
The tool calls the Federation directly - we're mounted INTO the same FastAPI
app that owns it - so there's no HTTP round-trip back to ourselves. Less wire,
less latency, fewer failure modes.
"""
