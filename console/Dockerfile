FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Read by the browser, so it's baked in at build time. ENGINE_URL and OFD_STAFF_KEY are read
# by the server when it runs, and never reach the browser.
ARG NEXT_PUBLIC_TRACES_URL=http://localhost:6006
ENV NEXT_PUBLIC_TRACES_URL=$NEXT_PUBLIC_TRACES_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/next.config.ts ./
COPY --from=build /app/.next ./.next
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
