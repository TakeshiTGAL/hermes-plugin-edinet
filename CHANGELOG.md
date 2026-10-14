# Changelog

## 0.1.1

`EDINET_API_KEY` was read from `os.environ` on each call, including when error text was scrubbed. One Hermes process can serve more than one profile, so that read can use another profile's API key. Each call now reads it with `agent.secret_scope.get_secret`. Each profile needs `EDINET_API_KEY` in that profile's `.env`. A profile without the key cannot call EDINET, even when another profile has it.

An HTTP proxy set in the environment is not used for EDINET requests. A machine that can reach the network only through a proxy can no longer reach EDINET.

`requires_hermes` is `>=0.21.6`, the same floor as the catalog entry already on main. This version was tested on Hermes 0.21.4 and later. Hermes 0.21.4 and 0.21.5 will not load version 0.1.1. Version 0.1.0's manifest allowed `>=0.21.4`.
