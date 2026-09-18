# OpenBull frontend — build the Vite SPA, serve it via nginx, and reverse-proxy
# API + WebSocket paths to the backend container (same-origin, so cookies work).
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
