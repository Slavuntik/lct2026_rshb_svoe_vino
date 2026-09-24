FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
COPY scripts/copy-runtime.mjs scripts/copy-runtime.mjs
RUN npm ci
COPY . .
RUN test -f public/models/manifest.json && npm run build
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
