from fastapi import FastAPI

app = FastAPI()

@app.get("/convert/")
def convert_number(number: float):
	number = number * 2
	return {"received_number": number}

if __name__ == "__main__":
	import uvicorn
	uvicorn.run(app, host="0.0.0.0", port=8000)
