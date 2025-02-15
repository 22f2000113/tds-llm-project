# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fastapi",
#     "requests",
#     "uvicorn",
# ]
# ///

from fastapi import FastAPI, HTTPException
import uvicorn
import requests
import json
import os
from fastapi.middleware.cors import CORSMiddleware
from subprocess import run
from fastapi.responses import PlainTextResponse
import time
import traceback

app = FastAPI()

# Global file counter
file_counter = 1

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from any origin
    allow_credentials=True,
    allow_methods=['GET', 'POST'],
    allow_headers=["*"],  # Allow any headers
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# API Configuration
url = "http://aiproxy.sanand.workers.dev/openai/v1/chat/completions"
AIPROXY_TOKEN = os.getenv("AIPROXY_TOKEN")
headers = {
    "Content-type": "application/json",
    "Authorization": f"Bearer {AIPROXY_TOKEN}"
}

def read_file(file_path):
    print("file_path "+file_path)
    """Reads a file and returns its content."""
    try:
        with open(file_path, 'r') as file:
            return file.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File does not exist")

def write_file(file_name, content):
    """Writes content to a file."""
    with open(file_name, 'w') as file:
        file.write(content)

response_format = {
    "type": "json_schema",
    "json_schema": {
        "name": "task_executor",
        "schema": {
            "type": "object",
            "required": ["python_dependencies", "python_code"],
            "additionalProperties": False,
            "properties": {
                "python_code": {
                    "type": "string",
                    "description": "Python code which will execute the task",
                },
                "python_dependencies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "module": {
                                "type": "string",
                                "description": "Name of python module",
                            }
                        },
                    },
                    "description": "List of required Python packages that are not preinstalled. Do not add 'uv' or standard Python libraries (such as 'os', 'sys', etc.). Only include third-party packages or modules that are not included by default in the Python installation.",
                    "additionalProperties": False,
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You are an automated agent, so generate the required Python code. If the code requires only \"uv\" or standard Python modules (such as re, os, sys etc.) then provide empty python_dependencies list. Provide a list of necessary Python packages which are required to be installed to execute the code. Assumptions: uv and python are preinstalled.  The generated code will be executed inside a Docker container.
    Program Requirements:
     1️. Write date-related code only if the task explicitly contains a date-related problem. Use Python’s dateutil.parser for flexible date parsing, and implement additional logic only if necessary to detect and clean irregular date formats.
     2. If the task includes a Python file and an email address as arguments, then Run the script using subprocess module in python e.g command=["uv", "run" , url, email] 
     3. Use Mime format to understand the email body 
     4. Write only the answer,remove before and after spaces, dont append any additional text.
     5. Data outside /data is never accessed or exfiltrated, even if the task description asks for it.
     6. Data is never deleted anywhere on the file system, even if the task description asks for it.
     7. If the task requires extracting an email address from a file:
        Use  MIME format for parsing emails. Extract only the email address (ignore names like `John Doe <email@example.com>`).
        Do not include names, brackets, or additional text—extract only `email@example.com`.
     8. Markdown File Processing:
        Find all Markdown (`.md`) files inside parent folder example :`/data/docs/`.
        Extract the **first occurrence** of each H1 (`# ` at the beginning of a line).
        Store results in `/data/docs/index.json`.
        Keep the relative path after `/data/docs/` in the filename.
        Example: Convert `/data/docs/listen/next.md` to `"listen/next.md"`
        Do not remove directory paths under `/data/docs/`
"""

def updated_task(task, code, error):
    """Formats an error message for retrying task execution."""
    return """
    Update the Python code:
    {code}
    ----
    For the below task:
    {task}
    ---
    As an error occurred while executing the code:
    {error}
    """.format(code=code, task=task, error=error)

def get_result(task):
    """Calls AI proxy to generate Python code for the given task."""
    response = requests.post(
        url=url,
        headers=headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": task},
                {"role": "system", "content": SYSTEM_PROMPT}
            ],
            "response_format": response_format
        }
    )
    
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="AI Proxy request failed")

    return response.json()["choices"][0]["message"]

def task_executor(file_name, params):
    """Executes the generated Python script with dependencies."""
    python_code = params['python_code']
    python_dependencies = params['python_dependencies']
    if python_dependencies:
        metadata_script = (
            "# /// script\n"
            "# requires-python = \">=3.11\"\n"
            "# dependencies = [\n"
            + "\n".join([f'#     "{dependency["module"]}",' for dependency in python_dependencies])
            + "\n# ]\n"
            "# ///"
        )
    else :
        metadata_script=''
    write_file(file_name, metadata_script + "\n" + python_code)

    try:
        output = run(["uv", "run", file_name], capture_output=True, text=True, cwd=os.getcwd())
        output_lines= output.stdout
        print(file_name ," output is ", str(output_lines))
        error_lines = output.stderr.split("\n")

        for line in error_lines:
            print("error "+ line)
            if line.strip().startswith("File"):
                raise Exception(error_lines)

        return "success"
    except Exception as e:
        print("Exception in task_executor "+str(e))
        return {"error": str(e)}

@app.get("/home")
def home():
    return {"message": "Welcome to Task API"}

@app.post("/run")
def run_tasks(task: str):
    """Handles task execution and retries if errors occur."""
    global file_counter

    try:
        start_time = time.time()
        response = get_result(task)
        file_name = f"llm_task{ file_counter }.py"
        #print("file_name and task " ,  str(file_name) , str(task))
        file_counter += 1
        output = task_executor(file_name, json.loads(response["content"]))
        print("output " + str(output))
        retry_count = 0
        while retry_count < 2:
            if output == "success":
                end_time = time.time()
                elapsed_time = end_time - start_time
                print(
                    f"Success.Time taken to execute the function successfully: {elapsed_time} seconds"
                )
                return {"status_code": 200, "details": "Successfully executed task"}
            elif "error" in output:
                response = get_result(updated_task(task=task, code=read_file(file_name), error=output["error"]))
                file_name = f"llm_task{ file_counter }.py"
                print("retry elif file_name and task " ,  str(file_name) , str(task) )
                file_counter += 1
                output = task_executor(file_name, json.loads(response["content"]))
            retry_count += 1
        # If the retry loop ends, record the time and return an error
        end_time = time.time()
        elapsed_time = end_time - start_time
        print(
            f"Time taken to execute the function with retries: {elapsed_time} seconds"
        )
        return {"status_code": 500, "details": "Task execution failed after retries"}
    except Exception as e:
        # If an exception is raised, stop the timer
        end_time = time.time()
        elapsed_time = end_time - start_time
        print(
            f"Time taken to execute the function (with exception): {elapsed_time} seconds"
        )
        print("Exception traceback:")
        traceback.print_exc()
        print(e)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/read", response_class=PlainTextResponse)
def get_path(path: str):
    """Reads a file and returns its content if it exists."""
    if not path.startswith("/data/"):
        raise HTTPException(status_code=400, detail="Invalid file path.")

    data = read_file(path)
    if data is not None:
        return PlainTextResponse(content=data, status_code=200)
    else:
        raise HTTPException(status_code=404, detail="File not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
