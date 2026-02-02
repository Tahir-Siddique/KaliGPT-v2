#!/env/bin python3

# /agents/utils/openserp_management.py
# Starts & Stop OpenSerp search service as a subprocess
# Last updated: 2 Feb 2026


import subprocess
import os
import argparse


# Define the working directory where openserp is located
working_directory = os.path.join(os.path.dirname(__file__), "/opt", "KaliGPT", "openserp")


def start_openserp_service():
    """
    Starts the OpenSerp search backend service using a subprocess.
    """

    try:
        # Define the command to start the OpenSerp service
        command = ["./openserp", "serve"]

        # Start the OpenSerp service as a subprocess
        process = subprocess.Popen(command, cwd=working_directory, stdout=subprocess.DEVNULL)

        # print(f"OpenSerp service started with PID: {process.pid}")
        return str(process.pid)

    except Exception as e:
        # print(f"Failed to start OpenSerp service: {e}")
        return ""


def stop_openserp_service(openserp_pid: str):
    """
    Stops the OpenSerp search backend service using its PID.

    Args:
        openserp_pid (str): The PID of the OpenSerp service to stop.

    Returns:
        int: 0 (True) if the service was stopped successfully, 1 (False) otherwise.
    """

    if openserp_pid :
        try:
            # Terminate the OpenSerp service
            subprocess.run(["kill", openserp_pid])
            # print(f"OpenSerp service with PID {openserp_pid} has been stopped.")
            return 0  # True

        except Exception as e:
            # print(f"Failed to stop OpenSerp service: {e}")
            return 1 # False

    else:
        # print("OpenSerp service is not running.")
        return 1



def main(options):
    """
    Main function to handle starting and stopping the OpenSerp service.
    """

    if options.start:
        print(start_openserp_service())

    elif options.stop and options.pid:
        print(stop_openserp_service(options.pid))

    else:
        print("No valid option provided. Use --start to start the service or --stop to stop it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the OpenSerp search backend service.")
    parser.add_argument("--start", action="store_true", help="Start the OpenSerp service.")
    parser.add_argument("--stop", action="store_true", help="Stop the OpenSerp service.")
    parser.add_argument("--pid", type=str, help="PID of the OpenSerp service to stop.")
    main(parser.parse_args())
