FROM node:22-slim
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
