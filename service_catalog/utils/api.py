"""Infrahub API client for the Service Catalog."""

from typing import Any, Dict, List, Optional

from infrahub_sdk import Config, InfrahubClientSync


class InfrahubAPIError(Exception):
    """Base exception for Infrahub API errors."""

    pass


class InfrahubConnectionError(InfrahubAPIError):
    """Exception raised when connection to Infrahub fails."""

    pass


class InfrahubHTTPError(InfrahubAPIError):
    """Exception raised for HTTP errors from Infrahub."""

    def __init__(self, message: str, status_code: int, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class InfrahubGraphQLError(InfrahubAPIError):
    """Exception raised for GraphQL errors from Infrahub."""

    def __init__(self, message: str, errors: List[Dict[str, Any]]):
        super().__init__(message)
        self.errors = errors


class InfrahubClient:
    """Client for interacting with the Infrahub API using the official SDK."""

    def __init__(
        self,
        base_url: str,
        api_token: Optional[str] = None,
        timeout: int = 30,
        ui_url: Optional[str] = None,
    ):
        """Initialize the Infrahub API client.

        Args:
            base_url: Base URL of the Infrahub instance (e.g., "http://localhost:8000")
            api_token: Optional API token for authentication (not currently used by SDK)
            timeout: Request timeout in seconds (default: 30)
            ui_url: Optional UI URL for generating browser links (defaults to base_url if not provided)
        """
        self.base_url = base_url.rstrip("/")
        self.ui_url = (ui_url or base_url).rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

        # Initialize the official Infrahub SDK client
        config = Config(timeout=timeout, api_token=api_token)
        self._client = InfrahubClientSync(address=base_url, config=config)

    def get_branches(self) -> List[Dict[str, Any]]:
        """Fetch all branches from Infrahub.

        Returns:
            List of branch dictionaries with keys: name, id, is_default, etc.

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            branches_dict = self._client.branch.all()
            # Convert to list of dicts for compatibility
            branches = []
            for branch_name, branch_data in branches_dict.items():
                branches.append(
                    {
                        "name": branch_name,
                        "id": branch_data.id,
                        "is_default": branch_data.is_default,
                        "sync_with_git": branch_data.sync_with_git,
                    }
                )
            return branches
        except Exception as e:
            raise InfrahubConnectionError(f"Failed to fetch branches: {str(e)}")

    def get_objects(self, object_type: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch objects of a specific type from Infrahub.

        Args:
            object_type: Type of object to fetch (e.g., "TopologyDataCenter")
            branch: Branch name to query (default: "main")

        Returns:
            List of object dictionaries

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        # Use specific methods for known types
        if object_type == "TopologyDataCenter":
            return self.get_datacenters(branch)
        elif object_type == "TopologyColocationCenter":
            return self.get_colocation_centers(branch)

        # Generic query for other types
        try:
            objects = self._client.filters(kind=object_type, branch=branch)
            # Convert SDK objects to dicts
            return [self._sdk_object_to_dict(obj) for obj in objects]
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch {object_type}: {str(e)}")

    def get_datacenters(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch TopologyDataCenter objects with all required fields.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of datacenter dictionaries with full field structure

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            datacenters = self._client.filters(kind="TopologyDataCenter", branch=branch, prefetch_relationships=True)

            result = []
            for dc in datacenters:
                dc_dict = {
                    "id": dc.id,
                    "name": {"value": getattr(dc.name, "value", None)},
                    "description": {
                        "value": getattr(dc.description, "value", None) if hasattr(dc, "description") else None
                    },
                    "strategy": {"value": getattr(dc.strategy, "value", None) if hasattr(dc, "strategy") else None},
                }

                # Add relationships if they exist
                if hasattr(dc, "location") and dc.location.peer:
                    dc_dict["location"] = {
                        "node": {
                            "id": dc.location.peer.id,
                            "display_label": str(dc.location.peer),
                        }
                    }

                if hasattr(dc, "design") and dc.design.peer:
                    design_peer = dc.design.peer
                    dc_dict["design"] = {
                        "node": {
                            "id": design_peer.id,
                            "name": {
                                "value": getattr(design_peer.name, "value", None)
                                if hasattr(design_peer, "name")
                                else None
                            },
                        }
                    }

                result.append(dc_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch datacenters: {str(e)}")

    def get_colocation_centers(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch TopologyColocationCenter objects with all required fields.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of colocation center dictionaries with full field structure

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            colocations = self._client.filters(
                kind="TopologyColocationCenter",
                branch=branch,
                prefetch_relationships=True,
            )

            result = []
            for colo in colocations:
                colo_dict = {
                    "id": colo.id,
                    "name": {"value": getattr(colo.name, "value", None)},
                    "description": {
                        "value": getattr(colo.description, "value", None) if hasattr(colo, "description") else None
                    },
                }

                # Add relationships if they exist
                if hasattr(colo, "location") and colo.location.peer:
                    colo_dict["location"] = {
                        "node": {
                            "id": colo.location.peer.id,
                            "display_label": str(colo.location.peer),
                        }
                    }

                if hasattr(colo, "provider"):
                    colo_dict["provider"] = {"value": getattr(colo.provider, "value", None)}

                result.append(colo_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch colocation centers: {str(e)}")

    def get_locations(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch LocationMetro objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of location dictionaries with id and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            locations = self._client.filters(kind="LocationMetro", branch=branch, prefetch_relationships=False)

            result = []
            for loc in locations:
                loc_dict = {
                    "id": loc.id,
                    "name": {"value": getattr(loc.name, "value", None)},
                }

                result.append(loc_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch locations: {str(e)}")

    def get_providers(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch OrganizationProvider objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of provider dictionaries with id and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            providers = self._client.filters(kind="OrganizationProvider", branch=branch, prefetch_relationships=False)

            result = []
            for provider in providers:
                provider_dict = {
                    "id": provider.id,
                    "name": {"value": getattr(provider.name, "value", None)},
                }

                result.append(provider_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch providers: {str(e)}")

    def get_designs(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch DesignTopology objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of design dictionaries with id and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            designs = self._client.filters(kind="DesignTopology", branch=branch, prefetch_relationships=False)

            result = []
            for design in designs:
                design_dict = {
                    "id": design.id,
                    "name": {"value": getattr(design.name, "value", None)},
                }

                result.append(design_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch designs: {str(e)}")

    def get_active_prefixes(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch active IpamPrefix objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of prefix dictionaries with id, prefix, and status

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Use GraphQL to filter for active prefixes
            query = """
            query GetActivePrefixes {
                IpamPrefix(status__value: "active") {
                    edges {
                        node {
                            id
                            prefix { value }
                            status { value }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, branch=branch)

            prefixes = []
            edges = result.get("IpamPrefix", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                prefixes.append(
                    {
                        "id": node.get("id"),
                        "prefix": {"value": node.get("prefix", {}).get("value")},
                        "status": {"value": node.get("status", {}).get("value")},
                    }
                )

            return prefixes
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch active prefixes: {str(e)}")

    def get_proposed_changes(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch proposed changes for a branch.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of proposed change dictionaries

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            pcs = self._client.filters(kind="CoreProposedChange", branch=branch)

            result = []
            for pc in pcs:
                pc_dict = {
                    "id": pc.id,
                    "name": {"value": getattr(pc.name, "value", None)},
                    "state": {"value": getattr(pc.state, "value", None)},
                }

                if hasattr(pc, "source_branch"):
                    pc_dict["source_branch"] = {"value": getattr(pc.source_branch, "value", None)}

                result.append(pc_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch proposed changes: {str(e)}")

    def execute_graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        branch: str = "main",
    ) -> Dict[str, Any]:
        """Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query or mutation string
            variables: Optional variables for the query
            branch: Branch name to execute against (default: "main")

        Returns:
            GraphQL response dictionary

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubGraphQLError: If GraphQL error occurs
        """
        try:
            result = self._client.execute_graphql(query=query, variables=variables, branch_name=branch)
            return result
        except Exception as e:
            raise InfrahubGraphQLError(f"GraphQL error: {str(e)}", [])

    def create_branch(self, branch_name: str, from_branch: str = "main", sync_with_git: bool = False) -> Dict[str, Any]:
        """Create a new branch in Infrahub.

        Args:
            branch_name: Name of the new branch
            from_branch: Branch to create from (default: "main")
            sync_with_git: Whether to sync with git (default: False)

        Returns:
            Dictionary with branch information

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            branch = self._client.branch.create(branch_name=branch_name, sync_with_git=sync_with_git)
            return {
                "name": branch.name,
                "id": branch.id,
                "is_default": branch.is_default,
            }
        except Exception as e:
            raise InfrahubAPIError(f"Failed to create branch: {str(e)}")

    def create_datacenter(self, branch: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a TopologyDataCenter object.

        Args:
            branch: Branch to create the object in
            data: Datacenter data dictionary with structure:
                - name: str
                - location: str (ID)
                - description: str
                - strategy: str
                - design: str
                - emulation: bool
                - provider: str
                - management_subnet: str (prefix ID)
                - customer_subnet: str (prefix ID)
                - technical_subnet: str (prefix ID)
                - member_of_groups: List[str]

        Returns:
            Created datacenter dictionary

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Create datacenter with references to existing prefixes
            dc_mutation = """
            mutation CreateDataCenter(
                $name: String!,
                $location: String!,
                $description: String,
                $strategy: String!,
                $design: String!,
                $emulation: Boolean,
                $provider: String!,
                $mgmt_prefix_id: String!,
                $cust_prefix_id: String!,
                $tech_prefix_id: String!,
                $groups: [RelatedNodeInput]
            ) {
                TopologyDataCenterUpsert(
                    data: {
                        name: { value: $name }
                        location: { id: $location }
                        description: { value: $description }
                        strategy: { value: $strategy }
                        design: { id: $design }
                        emulation: { value: $emulation }
                        provider: { id: $provider }
                        management_subnet: { id: $mgmt_prefix_id }
                        customer_subnet: { id: $cust_prefix_id }
                        technical_subnet: { id: $tech_prefix_id }
                        member_of_groups: $groups
                    }
                ) {
                    ok
                    object {
                        id
                        name { value }
                    }
                }
            }
            """

            # Convert group strings to RelatedNodeInput format
            groups = [{"id": group} for group in data.get("member_of_groups", [])]

            dc_variables = {
                "name": data["name"],
                "location": data["location"],
                "description": data.get("description", ""),
                "strategy": data["strategy"],
                "design": data["design"],
                "emulation": data.get("emulation", False),
                "provider": data["provider"],
                "mgmt_prefix_id": data["management_subnet"],
                "cust_prefix_id": data["customer_subnet"],
                "tech_prefix_id": data["technical_subnet"],
                "groups": groups,
            }

            # Create the datacenter
            dc_result = self.execute_graphql(dc_mutation, dc_variables, branch)

            # Extract datacenter info from result
            if dc_result.get("TopologyDataCenterUpsert", {}).get("ok"):
                dc_obj = dc_result["TopologyDataCenterUpsert"]["object"]
                return {"id": dc_obj["id"], "name": dc_obj["name"]}
            else:
                raise InfrahubAPIError(f"Failed to create datacenter: {dc_result}")

        except Exception as e:
            raise InfrahubAPIError(f"Failed to create datacenter: {str(e)}")

    def create_proposed_change(
        self, branch: str, name: str, description: str, destination_branch: str = "main"
    ) -> Dict[str, Any]:
        """Create a proposed change for a branch.

        Args:
            branch: Branch name (source branch)
            name: Proposed change name
            description: Proposed change description
            destination_branch: Target branch for the proposed change (default: "main")

        Returns:
            Dictionary with proposed change information

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            pc = self._client.create(
                kind="CoreProposedChange",
                branch=branch,
                name=name,
                description=description,
                source_branch=branch,
                destination_branch=destination_branch,
            )
            pc.save(allow_upsert=True)

            return {"id": pc.id, "name": name}
        except Exception as e:
            raise InfrahubAPIError(f"Failed to create proposed change: {str(e)}")

    def get_proposed_change_url(self, pc_id: str) -> str:
        """Get the URL for a proposed change.

        Args:
            pc_id: Proposed change ID

        Returns:
            URL string for the proposed change (uses UI URL for browser access)
        """
        return f"{self.ui_url}/proposed-changes/{pc_id}"

    def get_location_rows(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch LocationRow objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of LocationRow dictionaries with id and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            rows = self._client.filters(kind="LocationRow", branch=branch, prefetch_relationships=False)

            result = []
            for row in rows:
                row_dict = {
                    "id": row.id,
                    "name": {"value": getattr(row.name, "value", None)},
                }
                result.append(row_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch location rows: {str(e)}")

    def get_racks_by_row(self, row_id: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch LocationRack objects for a specific row.

        Args:
            row_id: LocationRow ID
            branch: Branch name to query (default: "main")

        Returns:
            List of LocationRack dictionaries with id, name, height, and row relationship

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Use GraphQL to filter racks by parent (row)
            query = """
            query GetRacksByRow($row_id: ID!) {
                LocationRack(parent__ids: [$row_id]) {
                    edges {
                        node {
                            id
                            name { value }
                            shortname { value }
                            parent {
                                node {
                                    id
                                }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, {"row_id": row_id}, branch)

            racks = []
            edges = result.get("LocationRack", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                racks.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                        "shortname": {"value": node.get("shortname", {}).get("value")},
                        # Default rack height to 42U (standard)
                        "height": {"value": 42},
                    }
                )

            return racks
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch racks for row: {str(e)}")

    def get_devices_by_rack(self, rack_id: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch DcimDevice objects for a specific rack.

        Args:
            rack_id: LocationRack ID
            branch: Branch name to query (default: "main")

        Returns:
            List of DcimDevice dictionaries with id, name, position, height, and device_type

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Use GraphQL to filter devices by location (rack)
            query = """
            query GetDevicesByRack($rack_id: ID!) {
                DcimDevice(location__ids: [$rack_id]) {
                    edges {
                        node {
                            id
                            name { value }
                            position { value }
                            role { value }
                            device_type {
                                node {
                                    name { value }
                                    height { value }
                                }
                            }
                            location {
                                node {
                                    id
                                }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, {"rack_id": rack_id}, branch)

            devices = []
            edges = result.get("DcimDevice", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})

                # Get height from device_type
                device_height = 1
                device_type_name = None
                device_type_node = node.get("device_type", {}).get("node")
                if device_type_node:
                    device_type_name = device_type_node.get("name", {}).get("value")
                    device_height = device_type_node.get("height", {}).get("value", 1)

                device_dict = {
                    "id": node.get("id"),
                    "name": {"value": node.get("name", {}).get("value")},
                    "position": {"value": node.get("position", {}).get("value")},
                    "height": {"value": device_height},
                    "role": {"value": node.get("role", {}).get("value")},
                }

                # Add device type if available
                if device_type_name:
                    device_dict["device_type"] = {"value": device_type_name}

                devices.append(device_dict)

            return devices
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch devices for rack: {str(e)}")

    def get_location_buildings(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch LocationBuilding objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of LocationBuilding dictionaries with id and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            buildings = self._client.filters(kind="LocationBuilding", branch=branch, prefetch_relationships=False)

            result = []
            for building in buildings:
                building_dict = {
                    "id": building.id,
                    "name": {"value": getattr(building.name, "value", None)},
                }
                result.append(building_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch location buildings: {str(e)}")

    def get_pods_by_building(self, building_id: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch LocationPod objects for a specific building.

        Args:
            building_id: LocationBuilding ID
            branch: Branch name to query (default: "main")

        Returns:
            List of LocationPod dictionaries with id, name, and parent relationship

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Use GraphQL to filter pods by parent (building)
            query = """
            query GetPodsByBuilding($building_id: ID!) {
                LocationPod(parent__ids: [$building_id]) {
                    edges {
                        node {
                            id
                            name { value }
                            parent {
                                node {
                                    id
                                }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, {"building_id": building_id}, branch)

            pods = []
            edges = result.get("LocationPod", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                pods.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                    }
                )

            return pods
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch pods for building: {str(e)}")

    def get_racks_by_pod(self, pod_id: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch LocationRack objects for a specific pod.

        Args:
            pod_id: LocationPod ID
            branch: Branch name to query (default: "main")

        Returns:
            List of LocationRack dictionaries with id, name, and parent relationship

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Use GraphQL to filter racks by parent (pod)
            query = """
            query GetRacksByPod($pod_id: ID!) {
                LocationRack(parent__ids: [$pod_id]) {
                    edges {
                        node {
                            id
                            name { value }
                            parent {
                                node {
                                    id
                                }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, {"pod_id": pod_id}, branch)

            racks = []
            edges = result.get("LocationRack", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                racks.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                    }
                )

            return racks
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch racks for pod: {str(e)}")

    def get_devices_by_location(
        self, pod_id: str, rack_id: Optional[str] = None, branch: str = "main"
    ) -> List[Dict[str, Any]]:
        """Fetch DcimDevice objects for a location.

        Args:
            pod_id: LocationPod ID
            rack_id: Optional LocationRack ID (None for all racks in pod)
            branch: Branch name to query (default: "main")

        Returns:
            List of DcimDevice dictionaries with id, name, and location

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Build query based on whether rack_id is provided
            if rack_id:
                # Filter by specific rack
                query = """
                query GetDevicesByRack($rack_id: ID!) {
                    DcimDevice(location__ids: [$rack_id]) {
                        edges {
                            node {
                                id
                                name { value }
                                location {
                                    node {
                                        id
                                    }
                                }
                            }
                        }
                    }
                }
                """
                variables = {"rack_id": rack_id}
            else:
                # Filter by pod (all racks in pod)
                query = """
                query GetDevicesByPod($pod_id: ID!) {
                    DcimDevice(location__ids: [$pod_id]) {
                        edges {
                            node {
                                id
                                name { value }
                                location {
                                    node {
                                        id
                                    }
                                }
                            }
                        }
                    }
                }
                """
                variables = {"pod_id": pod_id}

            result = self.execute_graphql(query, variables, branch)

            devices = []
            edges = result.get("DcimDevice", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                devices.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                    }
                )

            return devices
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch devices for location: {str(e)}")

    def get_interfaces_by_device(
        self, device_id: str, role_filter: Optional[str] = None, branch: str = "main"
    ) -> List[Dict[str, Any]]:
        """Fetch interface objects for a specific device.

        Args:
            device_id: DcimDevice ID
            role_filter: Optional role filter (e.g., "Customer")
            branch: Branch name to query (default: "main")

        Returns:
            List of interface dictionaries with id, name, description, and role

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            # Build query with optional role filter
            if role_filter:
                query = """
                query GetInterfacesByDevice($device_id: ID!, $role: String!) {
                    InfrahubInterface(device__ids: [$device_id], role__value: $role) {
                        edges {
                            node {
                                id
                                name { value }
                                description { value }
                                role { value }
                                device {
                                    node {
                                        id
                                    }
                                }
                            }
                        }
                    }
                }
                """
                variables = {"device_id": device_id, "role": role_filter}
            else:
                query = """
                query GetInterfacesByDevice($device_id: ID!) {
                    InfrahubInterface(device__ids: [$device_id]) {
                        edges {
                            node {
                                id
                                name { value }
                                description { value }
                                role { value }
                                device {
                                    node {
                                        id
                                    }
                                }
                            }
                        }
                    }
                }
                """
                variables = {"device_id": device_id}

            result = self.execute_graphql(query, variables, branch)

            interfaces = []
            edges = result.get("InfrahubInterface", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                interfaces.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                        "description": {"value": node.get("description", {}).get("value")},
                        "role": {"value": node.get("role", {}).get("value")},
                    }
                )

            return interfaces
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch interfaces for device: {str(e)}")

    def get_vlans_by_interface(self, interface_id: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch InterfaceVirtual (VLAN) objects assigned to an interface.

        Args:
            interface_id: Interface ID
            branch: Branch name to query (default: "main")

        Returns:
            List of VLAN dictionaries with id, vlan_id, and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            query = """
            query GetVLANsByInterface($interface_id: ID!) {
                InfrahubInterface(ids: [$interface_id]) {
                    edges {
                        node {
                            id
                            vlans {
                                edges {
                                    node {
                                        id
                                        vlan_id { value }
                                        name { value }
                                        description { value }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, {"interface_id": interface_id}, branch)

            vlans = []
            interface_edges = result.get("InfrahubInterface", {}).get("edges", [])

            if interface_edges:
                vlan_edges = interface_edges[0].get("node", {}).get("vlans", {}).get("edges", [])
                for edge in vlan_edges:
                    node = edge.get("node", {})
                    vlans.append(
                        {
                            "id": node.get("id"),
                            "vlan_id": {"value": node.get("vlan_id", {}).get("value")},
                            "name": {"value": node.get("name", {}).get("value")},
                            "description": {"value": node.get("description", {}).get("value")},
                        }
                    )

            return vlans
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch VLANs for interface: {str(e)}")

    def get_all_vlans(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch all InterfaceVirtual (VLAN) objects.

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of VLAN dictionaries with id, vlan_id, and name

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            vlans = self._client.filters(kind="InterfaceVirtual", branch=branch, prefetch_relationships=False)

            result = []
            for vlan in vlans:
                vlan_dict = {
                    "id": vlan.id,
                    "vlan_id": {"value": getattr(vlan.vlan_id, "value", None) if hasattr(vlan, "vlan_id") else None},
                    "name": {"value": getattr(vlan.name, "value", None)},
                    "description": {
                        "value": getattr(vlan.description, "value", None) if hasattr(vlan, "description") else None
                    },
                }
                result.append(vlan_dict)

            return result
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch VLANs: {str(e)}")

    def assign_vlan_to_interface(self, interface_id: str, vlan_id: str, branch: str) -> Dict[str, Any]:
        """Assign a VLAN to an interface using GraphQL mutation.

        Args:
            interface_id: Interface ID
            vlan_id: VLAN ID to assign
            branch: Branch to apply the change to

        Returns:
            Result dictionary with success status

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If mutation fails
        """
        try:
            # Use GraphQL mutation to update interface with VLAN
            mutation = """
            mutation AssignVLANToInterface($interface_id: String!, $vlan_id: String!) {
                InfrahubInterfaceUpdate(
                    data: {
                        id: $interface_id
                        vlans: [{ id: $vlan_id }]
                    }
                ) {
                    ok
                    object {
                        id
                        name { value }
                    }
                }
            }
            """

            variables = {"interface_id": interface_id, "vlan_id": vlan_id}

            result = self.execute_graphql(mutation, variables, branch)

            # Check if mutation was successful
            if result.get("InfrahubInterfaceUpdate", {}).get("ok"):
                return {
                    "success": True,
                    "interface": result["InfrahubInterfaceUpdate"]["object"],
                }
            else:
                raise InfrahubAPIError(f"VLAN assignment mutation failed: {result}")

        except Exception as e:
            raise InfrahubAPIError(f"Failed to assign VLAN to interface: {str(e)}")

    def get_organizations(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch OrganizationGeneric objects (customers, providers, etc.).

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of organization dictionaries with id, name, and type

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            query = """
            query GetOrganizations {
                OrganizationGeneric {
                    edges {
                        node {
                            id
                            display_label
                            __typename
                            ... on OrganizationCustomer {
                                name { value }
                            }
                            ... on OrganizationProvider {
                                name { value }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, branch=branch)

            organizations = []
            edges = result.get("OrganizationGeneric", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                organizations.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                        "display_label": node.get("display_label"),
                        "type": node.get("__typename"),
                    }
                )

            return organizations
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch organizations: {str(e)}")

    def get_deployments(self, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch TopologyDeployment objects (DataCenters, ColocationCenters, etc.).

        Args:
            branch: Branch name to query (default: "main")

        Returns:
            List of deployment dictionaries with id, name, and type

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            query = """
            query GetDeployments {
                TopologyDeployment {
                    edges {
                        node {
                            id
                            display_label
                            __typename
                            ... on TopologyDataCenter {
                                name { value }
                            }
                            ... on TopologyColocationCenter {
                                name { value }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, branch=branch)

            deployments = []
            edges = result.get("TopologyDeployment", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                deployments.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                        "display_label": node.get("display_label"),
                        "type": node.get("__typename"),
                    }
                )

            return deployments
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch deployments: {str(e)}")

    def create_network_segment(self, branch: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a ServiceNetworkSegment object.

        Args:
            branch: Branch to create the object in
            data: Network segment data dictionary with structure:
                - customer_name: str
                - environment: str (production, no-production)
                - segment_type: str (l2_only, l3_gateway, l3_vrf)
                - tenant_isolation: str (customer_dedicated, shared_controlled, public_shared)
                - vlan_id: int
                - deployment: str (ID)
                - owner: str (ID)
                - external_routing: bool (optional)
                - prefix: str (ID, optional)

        Returns:
            Created network segment dictionary

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            prefix = data.get("prefix")

            # Build mutation dynamically to exclude prefix when not provided,
            # since passing prefix: { id: null } causes server errors
            prefix_var = ""
            prefix_field = ""
            if prefix:
                prefix_var = "$prefix: String,"
                prefix_field = "prefix: { id: $prefix }"

            mutation = f"""
            mutation CreateNetworkSegment(
                $customer_name: String!,
                $environment: String!,
                $segment_type: String!,
                $tenant_isolation: String!,
                $vlan_id: BigInt!,
                $deployment: String!,
                $owner: String!,
                $group_id: String!,
                $external_routing: Boolean,
                {prefix_var}
            ) {{
                ServiceNetworkSegmentCreate(
                    data: {{
                        customer_name: {{ value: $customer_name }}
                        environment: {{ value: $environment }}
                        segment_type: {{ value: $segment_type }}
                        tenant_isolation: {{ value: $tenant_isolation }}
                        vlan_id: {{ value: $vlan_id }}
                        deployment: {{ id: $deployment }}
                        owner: {{ id: $owner }}
                        member_of_groups: [{{ id: $group_id }}]
                        external_routing: {{ value: $external_routing }}
                        {prefix_field}
                    }}
                ) {{
                    ok
                    object {{
                        id
                        name {{ value }}
                    }}
                }}
            }}
            """

            # Look up the network_segments group ID
            group_id = self._get_group_id("network_segments", branch)

            variables: Dict[str, Any] = {
                "customer_name": data["customer_name"],
                "environment": data["environment"],
                "segment_type": data["segment_type"],
                "tenant_isolation": data["tenant_isolation"],
                "vlan_id": data["vlan_id"],
                "deployment": data["deployment"],
                "owner": data["owner"],
                "group_id": group_id,
                "external_routing": data.get("external_routing", False),
            }

            if prefix:
                variables["prefix"] = prefix

            result = self.execute_graphql(mutation, variables, branch)

            if result.get("ServiceNetworkSegmentCreate", {}).get("ok"):
                segment_obj = result["ServiceNetworkSegmentCreate"]["object"]
                return {"id": segment_obj["id"], "name": segment_obj["name"]}
            else:
                raise InfrahubAPIError(f"Failed to create network segment: {result}")

        except Exception as e:
            raise InfrahubAPIError(f"Failed to create network segment: {str(e)}")

    def _get_group_id(self, group_name: str, branch: str) -> str:
        """Look up a CoreStandardGroup ID by name.

        Args:
            group_name: Name of the CoreStandardGroup.
            branch: Branch to query.

        Returns:
            The group ID.

        Raises:
            InfrahubAPIError: If the group is not found.
        """
        query = """
        query GetGroup($name: String!) {
            CoreStandardGroup(name__value: $name) {
                edges {
                    node {
                        id
                    }
                }
            }
        }
        """
        result = self.execute_graphql(query, {"name": group_name}, branch)
        edges = result.get("CoreStandardGroup", {}).get("edges", [])
        if not edges:
            raise InfrahubAPIError(f"Group '{group_name}' not found")
        return edges[0]["node"]["id"]

    def get_network_segments_by_deployment(self, deployment_id: str, branch: str = "main") -> List[Dict[str, Any]]:
        """Fetch ServiceNetworkSegment objects for a specific deployment.

        Args:
            deployment_id: TopologyDeployment (e.g., TopologyDataCenter) ID
            branch: Branch name to query (default: "main")

        Returns:
            List of network segment dictionaries with id, name, vlan_id, environment, etc.

        Raises:
            InfrahubConnectionError: If connection fails
            InfrahubAPIError: If API error occurs
        """
        try:
            query = """
            query GetNetworkSegmentsByDeployment($deployment_id: ID!) {
                ServiceNetworkSegment(deployment__ids: [$deployment_id]) {
                    edges {
                        node {
                            id
                            name { value }
                            customer_name { value }
                            environment { value }
                            segment_type { value }
                            tenant_isolation { value }
                            vlan_id { value }
                            owner {
                                node {
                                    id
                                    display_label
                                }
                            }
                        }
                    }
                }
            }
            """

            result = self.execute_graphql(query, {"deployment_id": deployment_id}, branch)

            segments = []
            edges = result.get("ServiceNetworkSegment", {}).get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                owner_node = node.get("owner", {}).get("node", {})
                segments.append(
                    {
                        "id": node.get("id"),
                        "name": {"value": node.get("name", {}).get("value")},
                        "customer_name": {"value": node.get("customer_name", {}).get("value")},
                        "environment": {"value": node.get("environment", {}).get("value")},
                        "segment_type": {"value": node.get("segment_type", {}).get("value")},
                        "tenant_isolation": {"value": node.get("tenant_isolation", {}).get("value")},
                        "vlan_id": {"value": node.get("vlan_id", {}).get("value")},
                        "owner": {"value": owner_node.get("display_label") if owner_node else None},
                    }
                )

            return segments
        except Exception as e:
            raise InfrahubAPIError(f"Failed to fetch network segments: {str(e)}")

    def _sdk_object_to_dict(self, obj: Any) -> Dict[str, Any]:
        """Convert an SDK object to a dictionary.

        Args:
            obj: SDK object

        Returns:
            Dictionary representation
        """
        return {
            "id": obj.id,
            "display_label": str(obj),
            "__typename": obj._schema.kind if hasattr(obj, "_schema") else None,
        }
