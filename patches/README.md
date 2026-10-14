# Viewer provenance and patch

This kit builds [yebo-liu/tdf](https://github.com/yebo-liu/tdf), pinned to commit
`91709652c18ed027dcb671200e8bf2dc7211dc2a` (2026-03-26).
That fork is derived from [itsjunetime/tdf](https://github.com/itsjunetime/tdf).
The viewer is licensed under **AGPL-3.0-only**. Its source and license are
available in the pinned upstream repository; this kit does not redistribute
the repository or viewer binary.

`tdf-socket-env.patch` changes only `src/ipc.rs::socket_path`: a nonempty
`TDF_SOCKET_PATH` environment variable takes precedence over the upstream
`/tmp/tdf-<PDF stem>.sock` default. The integration can therefore assign a
separate socket to each project and Neovim instance. This kit forwards commands
using the upstream Unix-socket wire format directly. The upstream
`--synctex-socket` CLI option also remains available.

The build script checks the pin and patch applicability, initializes recursive
Git submodules, and installs with `cargo install --locked --features synctex
--bin tdf`. Cargo.lock pins dependency versions; upstream's nightly toolchain
channel is not pinned to a date. The script copies this patch and upstream
commit record next to the installed viewer.

The upstream `s` key performs inverse search at the **center of the full PDF
page**, even after zooming or panning. It is not mouse-point inverse search.
The kit routes that action through `nvr`, avoiding upstream's unescaped
`nvim --remote-send` filename interpolation. The viewer must inherit the
LaTeX project's compilation directory so relative SyncTeX source names are
resolved correctly, including when PDFs are written to a build subdirectory.
