kill -9 $(lsof -i:8186 -t)
npm run build
python http_server.py