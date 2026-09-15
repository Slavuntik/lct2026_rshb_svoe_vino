# Мобильный интерфейс WineScan (Nuxt 4): сборка и Nitro-сервер на :3000.
# Адрес API задаётся при запуске: NUXT_API_BASE (в docker-compose — http://api:8080).
FROM node:22-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /web
ENV NODE_ENV=production PORT=3000 HOST=0.0.0.0
COPY --from=build /web/.output ./.output
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
