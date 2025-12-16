import subprocess
import sys


def download_es_core_news_sm_model() -> bool:
    """
    Downloads the 'es_core_news_sm' spaCy language model using subprocess.
    This function is specific to the 'es_core_news_sm' model.

    Returns:
        True if the model download command was executed successfully (or seemed to),
        False if an error occurred.
    """

    model_name = "es_core_news_sm"  # Hardcoded model name
    try:
        # Construct the command
        command = [sys.executable, "-m", "spacy", "download", model_name]

        print(f"Attempting to download spaCy model: {model_name}...")
        # Execute the command
        process = subprocess.run(
            command,
            check=True,  # Will raise a CalledProcessError if the command returns a non-zero exit code
            capture_output=True,  # Capture stdout and stderr
            text=True  # Decode stdout and stderr as text
        )
        # If check=True and the command fails, CalledProcessError is raised before this.
        print(f"Download command for '{model_name}' executed. Output:")
        print(process.stdout.strip())

        if "Successfully downloaded" in process.stdout or "already installed" in process.stdout:
             print(f"Model '{model_name}' appears to be successfully downloaded or was already present.")
             return True
        else:
            print(f"Command for '{model_name}' ran, but success message not found in output. Please check logs.")
            if process.stderr.strip():
                 print(f"Stderr: {process.stderr.strip()}")
            return False

    except subprocess.CalledProcessError as e:
        print(f"Error executing download command for {model_name}:")
        if e.stdout:
            print(f"Stdout: {e.stdout.strip()}")
        if e.stderr:
            print(f"Stderr: {e.stderr.strip()}")
        return False
    except FileNotFoundError:
        print(f"Error: The Python executable '{sys.executable}' or the 'spacy' command itself was not found.")
        print("Please ensure spaCy is installed and accessible in your Python environment's PATH.")
        return False
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred while trying to download {model_name}: {e}")
        return False
