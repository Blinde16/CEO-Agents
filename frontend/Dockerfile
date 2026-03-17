FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN npm install --silent

COPY . .
RUN npm run build

CMD ["npm", "start"]
