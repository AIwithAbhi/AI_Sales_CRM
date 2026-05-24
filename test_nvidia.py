"""Test NVIDIA API key and model availability."""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Test NVIDIA API
api_key = os.getenv("NVIDIA_API_KEY")
print(f"API Key found: {api_key[:10]}...{api_key[-4:] if api_key else 'None'}")

if not api_key:
    print("❌ NVIDIA_API_KEY not set in .env")
else:
    print("✅ API Key loaded")
    
    # Test API call - try different endpoints and models
    test_configs = [
        {
            "url": "https://integrate.api.nvidia.com/v1/chat/completions",
            "model": "meta/llama-3.1-70b-instruct",
        },
        {
            "url": "https://api.nvidia.com/v1/chat/completions",
            "model": "meta/llama-3.1-70b-instruct",
        },
    ]
    
    for config in test_configs:
        url = config["url"]
        model = config["model"]
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'API test successful' in JSON format: {\"status\": \"success\"}"}
            ],
            "max_tokens": 50,
            "temperature": 0,
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        try:
            print(f"\nTesting: {url}")
            print(f"Model: {model}")
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print("✅ API call successful!")
                print(f"Response: {result['choices'][0]['message']['content']}")
                break
            elif response.status_code == 401:
                print("❌ Authentication failed - Invalid API key")
                print("Get a new key from: https://build.nvidia.com/explore/discover")
            elif response.status_code == 429:
                print("❌ Rate limit exceeded - Too many requests")
            else:
                print(f"❌ API Error: {response.status_code}")
                print(f"Response: {response.text[:200]}")
                
        except Exception as e:
            print(f"❌ Error: {e}")
