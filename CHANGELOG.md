# Changelog

## 0.1.2

The first sentence Tool Search shows now uses the words a person would ask with. A sentence that starts by describing the mechanism is not found, and the model falls through to the built-in web_search.

Adds the `edinet-filings` skill. It tells the model to find the EDINET tools with tool_search before web_search. It does not set requires_toolsets, so it stays in the model's skill list when Tool Search is left at its default, which hides this plugin's tools.

Date separators, fullwidth digits in a date, a fullwidth securities code, and a fullwidth document id are accepted. A week date, a future date, and a document id of the wrong shape are refused with a fix and an example, and the caller's value is not copied into the message. Error results add `ok: false` and `error_type`.

## 0.1.1

`EDINET_API_KEY` was read from `os.environ` on each call, including when error text was scrubbed. One Hermes process can serve more than one profile, so that read can use another profile's API key. Each call now reads it with `agent.secret_scope.get_secret`. Each profile needs `EDINET_API_KEY` in that profile's `.env`. A profile without the key cannot call EDINET, even when another profile has it.

An HTTP proxy set in the environment is not used for EDINET requests. A machine that can reach the network only through a proxy can no longer reach EDINET.

`requires_hermes` is `>=0.21.6`, the same floor as the catalog entry already on main. This version was tested on Hermes 0.21.4 and later. Hermes 0.21.4 and 0.21.5 will not load version 0.1.1. Version 0.1.0's manifest allowed `>=0.21.4`.
