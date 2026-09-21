FROM node:20-bookworm-slim

ENV NODE_ENV=production \
    PORT=8090 \
    LX_SOURCES_DIR=/sources \
    DOWNLOAD_ROOT=/downloads \
    LX_CONFIG_DIR=/config

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY src ./src

RUN mkdir -p /config /sources /downloads
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8090/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "src/index.js"]
