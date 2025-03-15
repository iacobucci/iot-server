FROM python:3.13
WORKDIR /app
COPY . .
RUN pip install poetry && poetry install
CMD ["poetry", "run", "python", "main.py"]
