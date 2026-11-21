# Team "5-hour limit reached". TypeScript bot in ts/, based on the Wolverines' MIT-licensed bot
# (github.com/varchaskukreja/TTUN @ edf5776, mech-royale/bot; see ts/LICENSE). Rust alternative: Dockerfile.rust.
FROM oven/bun:1.4.4-slim@sha256:1846adf22496bd711036aae8be147e57bdebbaea2e5264e3e0b5af1db187d9a4
WORKDIR /bot
COPY ts/ /bot/
ENTRYPOINT ["bun", "/bot/bot.ts"]
