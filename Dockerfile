# Team "5-hour limit reached". Rust bot in rust/, based on BingBU's MIT-licensed bot
# (github.com/niranjankumarnk/mech-royale, see rust/LICENSE). Base images pinned by digest, crates by Cargo.lock.
FROM rust:1.100.0-slim-bookworm@sha256:7c7d4eb4d05b7781187a327809466cfddf29753f6defdc3d19a0e291951b7a37 AS build
WORKDIR /src
COPY rust/Cargo.toml rust/Cargo.lock ./
COPY rust/src ./src
RUN cargo build --release --locked --bin bot

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
COPY --from=build /src/target/release/bot /bot/bot
ENTRYPOINT ["/bot/bot"]
