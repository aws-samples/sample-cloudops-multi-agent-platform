"""
CUR Data Lake Query Tools - Lambda Implementation for AgentCore Gateway

This Lambda provides direct SQL query access to CUR (Cost and Usage Report) data
via AWS Athena. The database and table are pre-configured via environment variables
(CUR_DATABASE_NAME, CUR_TABLE_NAME) set during `make setup`.

Tool exposed to agent:
- start_query_execution: Execute SQL query on CUR data (use 'cur_data' as table name)

The agent uses 'cur_data' as a placeholder table name in SQL queries. The Lambda
uses the pre-configured database from CUR_DATABASE_NAME environment variable.

Architecture:
    Client -> Gateway (OAuth+MCP) -> Lambda (JSON) -> Athena API

Required IAM Permissions:
- athena:StartQueryExecution
- athena:GetQueryExecution
- athena:GetQueryResults
- athena:GetWorkGroup
- s3:GetBucketLocation (for results bucket)
- s3:GetObject (for query results)
- s3:PutObject (for query results)
- glue:GetDatabase, glue:GetTable, glue:GetPartitions (for table access)
"""

import json
import os
import threading

import boto3

# Cross-account support - shared module is packaged alongside lambda_function.py
try:
    from shared.cross_account import get_aws_client
except ImportError:
    # Fallback for single-account mode (shared module not present)
    def get_aws_client(service_name, region_name=None, **kwargs):
        client_kwargs = {"region_name": region_name} if region_name else {}
        client_kwargs.update(kwargs)
        return boto3.client(service_name, **client_kwargs)


def handler(event, context):
    """
    Main Lambda handler for Gateway MCP tools.

    Gateway passes tool name via context.client_context.custom["bedrockAgentCoreToolName"]
    in format: <target_name>___<tool_name>
    """
    print(f"Event: {json.dumps(event)}")

    # Get tool name from Gateway context
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]

    print(f"Tool name: {tool_name}")

    # Route to appropriate tool handler - only start_query_execution is exposed
    # list_databases and list_tables are not needed since database/table are pre-configured
    handlers = {
        "start_query_execution": handle_start_query_execution,
    }

    handler = handlers.get(tool_name)
    if handler:
        response = handler(event)
        print(f"Response: {json.dumps(response, default=str)}")
        return response
    else:
        error_response = {
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(handlers.keys()),
        }
        print(f"Response: {json.dumps(error_response)}")
        return error_response


def handle_start_query_execution(event):
    """
    Execute an Athena SQL query and wait for completion.

    This is a synchronous operation that:
    1. Starts the query execution
    2. Polls every 2 seconds until completion (max 15 minutes)
    3. Returns the query results automatically

    Parameters:
    - query_string: The SQL query to execute (required)
    - max_results: Maximum number of result rows to return (default: 100, max: 1000)

    Database, workgroup, and output location are configured via environment variables.
    The agent uses 'cur_data' as a placeholder table name - this function auto-substitutes
    it with the configured CUR_TABLE_NAME.
    """
    import time

    query_string = event.get("query_string")
    database = os.environ.get("CUR_DATABASE_NAME")
    table_name = os.environ.get("CUR_TABLE_NAME", "")
    workgroup = os.environ.get("ATHENA_WORKGROUP", "primary")
    output_location = os.environ.get("ATHENA_OUTPUT_LOCATION", "")
    max_results = min(event.get("max_results", 100), 1000)

    if not query_string:
        return {"error": "query_string parameter is required"}

    # Substitute 'cur_data' placeholder with actual table name
    if table_name:
        query_string = query_string.replace("cur_data", table_name)

    athena = get_aws_client("athena")

    try:
        # Step 1: Start query execution
        params = {"QueryString": query_string, "WorkGroup": workgroup}

        # Build QueryExecutionContext - only set Database, not Catalog
        # (uses default AWS Glue Data Catalog automatically)
        if database:
            params["QueryExecutionContext"] = {"Database": database}

        # Add output location if specified (otherwise uses workgroup default)
        if output_location:
            params["ResultConfiguration"] = {"OutputLocation": output_location}

        response = athena.start_query_execution(**params)
        query_execution_id = response["QueryExecutionId"]

        # Step 2: Poll for completion (max 15 minutes = 450 iterations * 2 seconds)
        max_iterations = 450
        for iteration in range(max_iterations):
            exec_response = athena.get_query_execution(
                QueryExecutionId=query_execution_id
            )
            status = exec_response["QueryExecution"]["Status"]["State"]

            if status == "SUCCEEDED":
                # Step 3: Get and return results
                result_params = {
                    "QueryExecutionId": query_execution_id,
                    "MaxResults": max_results,
                }

                result_response = athena.get_query_results(**result_params)

                # Parse results
                result_set = result_response.get("ResultSet", {})
                columns = []
                rows = []

                # Get column info
                if "ResultSetMetadata" in result_set:
                    columns = [
                        {"name": col.get("Name"), "type": col.get("Type")}
                        for col in result_set["ResultSetMetadata"].get("ColumnInfo", [])
                    ]

                # Get row data (first row is header for SELECT queries)
                raw_rows = result_set.get("Rows", [])
                is_first_row_header = True

                for row in raw_rows:
                    row_data = [
                        datum.get("VarCharValue", "") for datum in row.get("Data", [])
                    ]
                    if is_first_row_header:
                        is_first_row_header = False
                        continue  # Skip header row
                    rows.append(row_data)

                # Get statistics
                statistics = {}
                if "Statistics" in exec_response["QueryExecution"]:
                    stats = exec_response["QueryExecution"]["Statistics"]
                    statistics = {
                        "execution_time_ms": stats.get("EngineExecutionTimeInMillis"),
                        "data_scanned_bytes": stats.get("DataScannedInBytes"),
                        "total_time_ms": stats.get("TotalExecutionTimeInMillis"),
                    }

                # Include configured defaults for reference
                cur_config = {
                    "database": os.environ.get("CUR_DATABASE_NAME", ""),
                    "table": os.environ.get("CUR_TABLE_NAME", ""),
                }

                return {
                    "status": "success",
                    "query_execution_id": query_execution_id,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "statistics": statistics,
                    "next_token": result_response.get("NextToken"),
                    "message": f"Query completed successfully in {iteration * 2} seconds",
                    "cur_config": cur_config,
                }

            elif status in ["FAILED", "CANCELLED"]:
                error_reason = exec_response["QueryExecution"]["Status"].get(
                    "StateChangeReason", "Unknown error"
                )
                return {
                    "error": f"Query {status.lower()}",
                    "reason": error_reason,
                    "query_execution_id": query_execution_id,
                    "status": status,
                }

            # Still running, wait 2 seconds before next check
            threading.Event().wait(2)

        # Timeout after 15 minutes
        return {
            "error": "Query execution timeout",
            "message": "Query did not complete within 15 minutes",
            "query_execution_id": query_execution_id,
            "status": "TIMEOUT",
        }

    except Exception as e:
        return {"error": str(e)}


def handle_get_query_execution(event):
    """
    Get the status and details of a query execution.

    Parameters:
    - query_execution_id: The unique ID of the query execution (required)
    """
    query_execution_id = event.get("query_execution_id")

    if not query_execution_id:
        return {"error": "query_execution_id parameter is required"}

    athena = get_aws_client("athena")

    try:
        response = athena.get_query_execution(QueryExecutionId=query_execution_id)

        execution = response["QueryExecution"]
        status = execution["Status"]

        result = {
            "query_execution_id": query_execution_id,
            "status": status["State"],
            "query": execution.get("Query", ""),
            "database": execution.get("QueryExecutionContext", {}).get("Database"),
            "catalog": execution.get("QueryExecutionContext", {}).get("Catalog"),
            "workgroup": execution.get("WorkGroup"),
            "submission_time": str(status.get("SubmissionDateTime", "")),
            "completion_time": str(status.get("CompletionDateTime", "")),
        }

        # Add statistics if available
        if "Statistics" in execution:
            stats = execution["Statistics"]
            result["statistics"] = {
                "engine_execution_time_ms": stats.get("EngineExecutionTimeInMillis"),
                "data_scanned_bytes": stats.get("DataScannedInBytes"),
                "total_execution_time_ms": stats.get("TotalExecutionTimeInMillis"),
                "query_queue_time_ms": stats.get("QueryQueueTimeInMillis"),
                "service_processing_time_ms": stats.get(
                    "ServiceProcessingTimeInMillis"
                ),
            }

        # Add error info if failed
        if status["State"] == "FAILED":
            result["error_message"] = status.get("StateChangeReason", "Unknown error")

        # Add output location if available
        if "ResultConfiguration" in execution:
            result["output_location"] = execution["ResultConfiguration"].get(
                "OutputLocation"
            )

        return result
    except Exception as e:
        return {"error": str(e), "query_execution_id": query_execution_id}


def handle_get_query_results(event):
    """
    Get the results of a completed query execution.

    Parameters:
    - query_execution_id: The unique ID of the query execution (required)
    - max_results: Maximum number of results to return (default: 100)
    - next_token: Token for pagination
    """
    query_execution_id = event.get("query_execution_id")
    max_results = event.get("max_results", 100)
    next_token = event.get("next_token")

    if not query_execution_id:
        return {"error": "query_execution_id parameter is required"}

    athena = get_aws_client("athena")

    try:
        # First check if query is complete
        exec_response = athena.get_query_execution(QueryExecutionId=query_execution_id)
        status = exec_response["QueryExecution"]["Status"]["State"]

        if status != "SUCCEEDED":
            return {
                "error": f"Query is not complete. Current status: {status}",
                "query_execution_id": query_execution_id,
                "status": status,
            }

        # Get results
        params = {
            "QueryExecutionId": query_execution_id,
            "MaxResults": min(max_results, 1000),  # Athena max is 1000
        }

        if next_token:
            params["NextToken"] = next_token

        response = athena.get_query_results(**params)

        # Parse results
        result_set = response.get("ResultSet", {})
        columns = []
        rows = []

        # Get column info
        if "ResultSetMetadata" in result_set:
            columns = [
                {"name": col.get("Name"), "type": col.get("Type")}
                for col in result_set["ResultSetMetadata"].get("ColumnInfo", [])
            ]

        # Get row data (first row is header for SELECT queries)
        raw_rows = result_set.get("Rows", [])
        is_first_row_header = True

        for row in raw_rows:
            row_data = [datum.get("VarCharValue", "") for datum in row.get("Data", [])]
            if is_first_row_header:
                is_first_row_header = False
                continue  # Skip header row
            rows.append(row_data)

        return {
            "query_execution_id": query_execution_id,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "next_token": response.get("NextToken"),
        }
    except Exception as e:
        return {"error": str(e), "query_execution_id": query_execution_id}


def handle_list_query_executions(event):
    """
    List recent query executions.

    Parameters:
    - workgroup: Filter by workgroup (default: primary)
    - max_results: Maximum number of results (default: 50)
    - next_token: Token for pagination
    """
    workgroup = event.get("workgroup") or os.environ.get("ATHENA_WORKGROUP", "primary")
    max_results = event.get("max_results", 50)
    next_token = event.get("next_token")

    athena = get_aws_client("athena")

    try:
        params = {
            "WorkGroup": workgroup,
            "MaxResults": min(max_results, 50),  # Athena max is 50
        }

        if next_token:
            params["NextToken"] = next_token

        response = athena.list_query_executions(**params)

        query_ids = response.get("QueryExecutionIds", [])

        # Get details for each query
        executions = []
        if query_ids:
            batch_response = athena.batch_get_query_execution(
                QueryExecutionIds=query_ids
            )

            for execution in batch_response.get("QueryExecutions", []):
                status = execution.get("Status", {})
                executions.append(
                    {
                        "query_execution_id": execution.get("QueryExecutionId"),
                        "status": status.get("State"),
                        "query": (
                            execution.get("Query", "")[:200] + "..."
                            if len(execution.get("Query", "")) > 200
                            else execution.get("Query", "")
                        ),
                        "database": execution.get("QueryExecutionContext", {}).get(
                            "Database"
                        ),
                        "submission_time": str(status.get("SubmissionDateTime", "")),
                        "workgroup": execution.get("WorkGroup"),
                    }
                )

        return {
            "workgroup": workgroup,
            "executions": executions,
            "count": len(executions),
            "next_token": response.get("NextToken"),
        }
    except Exception as e:
        return {"error": str(e)}


def handle_list_databases(event):
    """
    List databases in a data catalog.

    Parameters:
    - catalog: Data catalog name (default: AwsDataCatalog)
    - max_results: Maximum number of results (default: 50)
    - next_token: Token for pagination
    """
    catalog = event.get("catalog", "AwsDataCatalog")
    max_results = event.get("max_results", 50)
    next_token = event.get("next_token")

    athena = get_aws_client("athena")

    try:
        params = {"CatalogName": catalog, "MaxResults": min(max_results, 50)}

        if next_token:
            params["NextToken"] = next_token

        response = athena.list_databases(**params)

        databases = [
            {
                "name": db.get("Name"),
                "description": db.get("Description", ""),
                "parameters": db.get("Parameters", {}),
            }
            for db in response.get("DatabaseList", [])
        ]

        return {
            "catalog": catalog,
            "databases": databases,
            "count": len(databases),
            "next_token": response.get("NextToken"),
        }
    except Exception as e:
        return {"error": str(e), "catalog": catalog}


def handle_list_tables(event):
    """
    List tables in a database.

    Parameters:
    - database: Database name (default: CUR_DATABASE_NAME env var)
    - catalog: Data catalog name (default: AwsDataCatalog)
    - max_results: Maximum number of results (default: 50)
    - next_token: Token for pagination
    """
    database = event.get("database") or os.environ.get("CUR_DATABASE_NAME")
    catalog = event.get("catalog", "AwsDataCatalog")
    max_results = event.get("max_results", 50)
    next_token = event.get("next_token")

    if not database:
        return {
            "error": "database parameter is required (or set CUR_DATABASE_NAME env var)"
        }

    athena = get_aws_client("athena")

    try:
        params = {
            "CatalogName": catalog,
            "DatabaseName": database,
            "MaxResults": min(max_results, 50),
        }

        if next_token:
            params["NextToken"] = next_token

        response = athena.list_table_metadata(**params)

        tables = [
            {
                "name": table.get("Name"),
                "type": table.get("TableType"),
                "columns": [
                    {"name": col.get("Name"), "type": col.get("Type")}
                    for col in table.get("Columns", [])
                ],
                "partition_keys": [
                    {"name": pk.get("Name"), "type": pk.get("Type")}
                    for pk in table.get("PartitionKeys", [])
                ],
                "create_time": str(table.get("CreateTime", "")),
            }
            for table in response.get("TableMetadataList", [])
        ]

        return {
            "catalog": catalog,
            "database": database,
            "tables": tables,
            "count": len(tables),
            "next_token": response.get("NextToken"),
        }
    except Exception as e:
        return {"error": str(e), "database": database}


def handle_get_table_metadata(event):
    """
    Get detailed metadata about a specific table.

    Parameters:
    - database: Database name (required)
    - table: Table name (required)
    - catalog: Data catalog name (default: AwsDataCatalog)
    """
    database = event.get("database")
    table = event.get("table")
    catalog = event.get("catalog", "AwsDataCatalog")

    if not database:
        return {"error": "database parameter is required"}
    if not table:
        return {"error": "table parameter is required"}

    athena = get_aws_client("athena")

    try:
        response = athena.get_table_metadata(
            CatalogName=catalog, DatabaseName=database, TableName=table
        )

        table_meta = response.get("TableMetadata", {})

        return {
            "catalog": catalog,
            "database": database,
            "table": table,
            "name": table_meta.get("Name"),
            "type": table_meta.get("TableType"),
            "columns": [
                {
                    "name": col.get("Name"),
                    "type": col.get("Type"),
                    "comment": col.get("Comment", ""),
                }
                for col in table_meta.get("Columns", [])
            ],
            "partition_keys": [
                {
                    "name": pk.get("Name"),
                    "type": pk.get("Type"),
                    "comment": pk.get("Comment", ""),
                }
                for pk in table_meta.get("PartitionKeys", [])
            ],
            "parameters": table_meta.get("Parameters", {}),
            "create_time": str(table_meta.get("CreateTime", "")),
        }
    except Exception as e:
        return {"error": str(e), "database": database, "table": table}


def handle_stop_query_execution(event):
    """
    Stop a running query execution.

    Parameters:
    - query_execution_id: The unique ID of the query execution to stop (required)
    """
    query_execution_id = event.get("query_execution_id")

    if not query_execution_id:
        return {"error": "query_execution_id parameter is required"}

    athena = get_aws_client("athena")

    try:
        athena.stop_query_execution(QueryExecutionId=query_execution_id)

        return {
            "query_execution_id": query_execution_id,
            "status": "CANCELLED",
            "message": "Query execution stop request submitted successfully",
        }
    except Exception as e:
        return {"error": str(e), "query_execution_id": query_execution_id}
