# Resource claim fields

Use claims when reserving mutable resources or declaring disposable check effects.
Each claim has `kind`, `key`, `mode` and, only for service/port claims, `host`.
`mode` is `read` or `write`; lists contain 1–64 claims unless an operation documents
a narrower bound. Keys are nonempty strings of at most 4,096 characters.

`path`, `git`, `build` and `database` keys are absolute local paths. The runtime
derives canonical paths, inode identity and ancestors; do not supply those fields.
It detects aliases and overlapping ancestors conservatively.

`service` and `port` require `host:"local"`. A service key names the shared service.
A port key is `tcp:N` or `udp:N`, with N from 1 through 65535. Names are normalized
and case-folded. Unknown hosts or resource kinds reject. These reservations
coordinate participating writers; they do not constrain arbitrary processes.
