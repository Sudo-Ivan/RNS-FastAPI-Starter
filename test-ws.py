import asyncio
import websockets
import json
import sys

async def listen_for_messages():
    uri = "ws://localhost:8000/ws"
    
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("Connected to LXMF API websocket")
                print("Waiting for messages...")
                
                while True:
                    try:
                        message = await websocket.recv()
                        data = json.loads(message)
                        print("\nNew message received:")
                        print(f"From: {data['sender']}")
                        print(f"Content: {data['content']}")
                        print(f"Hash: {data['hash']}")
                    except websockets.ConnectionClosed:
                        print("Connection closed, attempting to reconnect...")
                        break
                    except Exception as e:
                        print(f"Error: {e}")
                        break
                        
        except KeyboardInterrupt:
            print("\nShutting down...")
            sys.exit(0)
        except Exception as e:
            print(f"Connection error: {e}")
            print("Retrying in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(listen_for_messages())
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)