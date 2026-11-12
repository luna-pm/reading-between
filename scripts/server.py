"""MCP server for 阅读之间. Default: stdio; private HTTP via explicit --http."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from store import Store, Source, Node, Edge, Capture

ROOT=Path(__file__).resolve().parent.parent
UI='ui://reading-between/map.html'
server=FastMCP('reading-between',instructions='Accumulate reading insights with explicit provenance, semantic links and revision history. The host model does reasoning; this server persists and retrieves. Never invent conversation access or successful writes.',host='127.0.0.1',port=8787,stateless_http=True,json_response=True)
def db():return Store()
def result(data):return CallToolResult(content=[TextContent(type='text',text=json.dumps(data,ensure_ascii=False))],structuredContent=data)
READ=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False)
WRITE=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False,idempotentHint=True)
@server.resource(UI,name='reading-between-map',mime_type='text/html;profile=mcp-app',meta={'ui':{'prefersBorder':False,'csp':{'connectDomains':[],'resourceDomains':[]}}})
def map_resource():return (ROOT/'assets'/'map.html').read_text()
@server.tool(annotations=READ,meta={'ui':{'resourceUri':UI}})
def get_knowledge_map() -> CallToolResult:
    """Open the interactive reading knowledge map, or read all active nodes, links, revision and recent changes before saving. Use when a user wants their map or previous reading thoughts."""
    return result({'graph':db().view()})
@server.tool(annotations=READ)
def find_related(query:str,limit:int=12) -> CallToolResult:
    """Retrieve existing reading notes before capturing new thoughts. Search multiple related terms (space separated), then use host reasoning for links; results are lexical candidates, not semantic certainty."""
    return result(db().search(query,limit))
@server.tool(annotations=WRITE)
def capture_reading_batch(request_id:str,expected_revision:int,source:Source,nodes:list[Node],edges:list[Edge]) -> CallToolResult:
    """Save valuable reading thoughts/new knowledge and evidence-backed links during an enabled reading workflow without asking users to tag or request each save. Read current revision first. Preserve user questions and AI proposals. Retry an uncertain write with the SAME request_id and payload. Do not use for every casual remark."""
    return result(db().capture(Capture(request_id=request_id,expected_revision=expected_revision,source=source,nodes=nodes,edges=edges)))
@server.tool(annotations=WRITE,meta={'ui':{'resourceUri':UI}})
def revise_reading_node(node_id:str,title:str,body:str,status:Literal['open','proposed','accepted','verified'],request_id:str,expected_revision:int) -> CallToolResult:
    """Edit the selected reading note with optimistic concurrency. Preserve original quotes and provenance; modifications remain in revision history. Mark accepted only with user evidence; verified only for externally checked claims."""
    return result(db().revise(node_id,title,body,status,request_id,expected_revision))
@server.tool(annotations=WRITE,meta={'ui':{'resourceUri':UI}})
def archive_reading_node(node_id:str,request_id:str,expected_revision:int) -> CallToolResult:
    """Reversibly archive a reading node and hide incident edges. Use only when the user removes a note or it is a confirmed duplicate. This is not a permanent deletion."""
    return result(db().archive(node_id,request_id,expected_revision))
@server.tool(annotations=WRITE,meta={'ui':{'resourceUri':UI}})
def undo_reading_change(change_id:int,request_id:str,expected_revision:int) -> CallToolResult:
    """Undo the latest graph-changing operation, preserving a new history entry. Read the latest revision and change id first; reject stale or non-latest undo requests."""
    return result(db().undo(change_id,request_id,expected_revision))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--http',action='store_true');args=parser.parse_args()
    server.run(transport='streamable-http' if args.http else 'stdio')
