# ---------- build stage ----------
# Build the static production bundle with Vite.
FROM node:22-alpine AS build
WORKDIR /app

# Install dependencies from the lockfile for reproducible builds.
COPY package*.json ./
RUN npm ci

# Build the app -> /app/dist
COPY . .
RUN npm run build

# ---------- serve stage ----------
# Serve the static bundle with a tiny nginx image.
FROM nginx:alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
