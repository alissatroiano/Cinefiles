import vertexai
from vertexai import agent_engines

class CinefilesAgentRunner:
    def __init__(self, project_id: str, location: str):
        vertexai.init(project=project_id, location=location)
        # Initialize your active agent engine/runtime resource here
        self.client = agent_engines.get_engine(...) # or your active execution handle

    async def run_clearance_workflow(self, query: str):
        # Active runtime call utilizing the SDK during app execution
        response = self.client.query(input=query)
        return response