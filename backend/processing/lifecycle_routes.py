"""Separate, disabled-by-default continuous event protocol."""
from fastapi import APIRouter, Depends, Response, Query
from backend.processing.routes import processor
from backend.processing import lifecycle, capture, receipts

router=APIRouter(prefix='/api/processing/continuous-events',tags=['processing'])

def acknowledged(result,body,kind,camera_id=None):
    # Call only after the service has committed or verified an exact retry.
    # Current lifecycle revision can be newer than the acknowledged command.
    return {**result,'accepted_command':{'kind':kind,'session_id':body.session_id,
            'sequence':0 if kind=='start' else body.sequence,'camera_id':camera_id}}


@router.post('')
def start(body: lifecycle.Start, response: Response, node: dict=Depends(processor)):
    result=lifecycle.start(body,node['node_id'])
    response.status_code=201 if result['created'] else 200
    return acknowledged(result,body,'start')

@router.get('/reference-requests')
def references(node: dict=Depends(processor)):
    return lifecycle.reference_requests(node['node_id'])

@router.get('/active')
def active(after: str='',limit: int=Query(default=50,ge=1,le=100),node: dict=Depends(processor)):
    return capture.discovery(node['node_id'],after,limit)

@router.post('/{event_id}/cameras/{camera_id}/capture-reports')
def capture_report(event_id: str,camera_id: str,body: capture.Report,node: dict=Depends(processor)):
    return acknowledged(capture.report(event_id,camera_id,body,node['node_id']),body,'capture_report',camera_id)

@router.get('/{event_id}')
def read(event_id: str,session_id: str,node: dict=Depends(processor)):
    return lifecycle.read(event_id,node['node_id'],session_id)

@router.post('/{event_id}/updates')
def update(event_id: str,body: lifecycle.Update,node: dict=Depends(processor)):
    return acknowledged(lifecycle.update(event_id,body,node['node_id']),body,body.kind)

@router.post('/{event_id}/segments')
def segment(event_id: str,body: lifecycle.Segment,node: dict=Depends(processor)):
    return acknowledged(lifecycle.segment(event_id,body,node['node_id']),body,'manifest')


@router.get('/{event_id}/receipts/{request_id}')
def receipt(event_id: str,request_id: str,session_id: str,response: Response,node: dict=Depends(processor)):
    response.headers['Cache-Control']='private, no-store'
    return receipts.read(event_id,request_id,node['node_id'],session_id)
