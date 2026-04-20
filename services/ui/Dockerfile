# Build stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
ARG VITE_AGENT_API_URL=/api
ARG VITE_DD_RUM_APP_ID=""
ARG VITE_DD_RUM_CLIENT_TOKEN=""
ARG VITE_DD_RUM_SITE=us3.datadoghq.com
ARG VITE_DD_ENV=dev
ENV VITE_AGENT_API_URL=$VITE_AGENT_API_URL
ENV VITE_DD_RUM_APP_ID=$VITE_DD_RUM_APP_ID
ENV VITE_DD_RUM_CLIENT_TOKEN=$VITE_DD_RUM_CLIENT_TOKEN
ENV VITE_DD_RUM_SITE=$VITE_DD_RUM_SITE
ENV VITE_DD_ENV=$VITE_DD_ENV
RUN npm run build

# Serve stage — NGINX
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
