# Build from the monorepo root: docker build -f apps/shelf-finder/Dockerfile .
FROM node:22-bookworm-slim AS build
WORKDIR /repo/apps/shelf-finder
COPY apps/shelf-finder/package*.json ./
COPY apps/shelf-finder/scripts/copy-runtime.mjs scripts/copy-runtime.mjs
RUN npm ci
COPY apps/shelf-finder .
COPY apps/web/src/styles /repo/apps/web/src/styles
COPY apps/web/src/assets/fonts /repo/apps/web/src/assets/fonts
COPY apps/web/public/brand/logo-svoe-vino.svg /repo/apps/web/public/brand/logo-svoe-vino.svg
RUN npm run build
FROM nginx:alpine
COPY --from=build /repo/apps/shelf-finder/dist /usr/share/nginx/html
EXPOSE 80
