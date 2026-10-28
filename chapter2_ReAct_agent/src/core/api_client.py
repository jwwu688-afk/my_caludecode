import os
from openai import OpenAI
from typing import Dict, Any, Optional
from dotenv import load_dotenv


class APIClient:
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """
        Initialize the API client as a singleton using environment variables.
        """
        if not self._initialized:
            # Load the .env file
            load_dotenv()
            
            # Read config from environment variables, raising if any are missing
            self.api_key = self._get_required_env_var("OPENAI_API_KEY")
            self.base_url = self._get_required_env_var("OPENAI_BASE_URL") 
            self.model = self._get_required_env_var("OPENAI_MODEL")
            
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            self._initialized = True
    
    def _get_required_env_var(self, var_name: str) -> str:
        """
        Get a required environment variable or raise an error if it is missing.
        
        Args:
            var_name: Environment variable name
            
        Returns:
            The environment variable value
            
        Raises:
            ValueError: When the environment variable is missing or empty
        """
        value = os.getenv(var_name)
        if not value:
            raise ValueError(f"Environment variable {var_name} is not set or is empty. Please check your .env file.")
        return value
    
    def get_completion(self, request_params: Dict[str, Any]):
        """
        Send a chat completion request and return the message.
        
        Args:
            request_params: Request parameters including model, messages, and related fields
            
        Returns:
            The AI assistant response message object
        """
        request_params["model"] = self.model
        try:
            response = self.client.chat.completions.create(**request_params)
            return response.choices[0].message
        except Exception as e:
            raise Exception(f"API request failed: {str(e)}")
