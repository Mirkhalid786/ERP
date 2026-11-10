FROM node:22-alpine AS base

WORKDIR /app

# Install dependencies layer (cached separately from source)
COPY package.json package-lock.json* ./
RUN npm ci --frozen-lockfile

# Copy source
COPY . .

# Development server
EXPOSE 3000
CMD ["npm", "run", "dev"]
