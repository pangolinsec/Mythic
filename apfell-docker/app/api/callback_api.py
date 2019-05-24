from app import apfell, db_objects
from sanic.response import json
from app.database_models.model import Callback, Task, Response, LoadedCommands, PayloadCommand, Command
from sanic_jwt.decorators import scoped, inject_user
import app.database_models.model as db_model
from sanic.exceptions import abort


@apfell.route(apfell.config['API_BASE'] + "/callbacks/", methods=['GET'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user'], False)  # user or user-level api token are ok
async def get_all_callbacks(request, user):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    if user['current_operation'] != "":
        query = await db_model.operation_query()
        operation = await db_objects.get(query, name=user['current_operation'])
        query = await db_model.callback_query()
        callbacks = await db_objects.execute(query.where(Callback.operation == operation))
        return json([c.to_json() for c in callbacks])
    else:
        return json([])


# this one is specifically not @protect or @inject_user because our callback needs to be able to access this
#   and we don't know when the callback will actually happen, so we don't want the JWT to be timed out
@apfell.route(apfell.config['API_BASE'] + "/callbacks/", methods=['POST'])
async def create_callback(request):
    return json(await create_callback_func(request.json))


async def create_callback_func(data):
    if not data:
        return {'status': 'error', 'error': "Data is required for POST"}
    if 'user' not in data:
        return {'status': 'error', 'error': 'User required'}
    if 'host' not in data:
        return {'status': 'error', 'error': 'Host required'}
    if 'pid' not in data:
        return {'status': 'error', 'error': 'PID required'}
    if 'ip' not in data:
        return {'status': 'error', 'error': 'IP required'}
    if 'uuid' not in data:
        return {'status': 'error', 'error': 'uuid required'}
    # Get the corresponding Payload object based on the uuid
    try:
        query = await db_model.payload_query()
        payload = await db_objects.get(query, uuid=data['uuid'])
        pcallback = None
    except Exception as e:
        print(e)
        return {}
    try:
        cal = await db_objects.create(Callback, user=data['user'], host=data['host'], pid=data['pid'],
                                      ip=data['ip'], description=payload.tag, operator=payload.operator,
                                      registered_payload=payload, pcallback=pcallback, operation=payload.operation)
        if 'encryption_type' in data:
            cal.encryption_type = data['encryption_type']
        if 'decryption_key' in data:
            cal.decryption_key = data['decryption_key']
        if 'encryption_key' in data:
            cal.encryption_key = data['encryption_key']
        await db_objects.update(cal)
        query = await db_model.payloadcommand_query()
        payload_commands = await db_objects.execute(query.where(PayloadCommand.payload == payload))
        # now create a loaded command for each one since they are loaded by default
        for p in payload_commands:
            await db_objects.create(LoadedCommands, command=p.command, version=p.version, callback=cal, operator=payload.operator)
    except Exception as e:
        print(e)
        return {'status': 'error', 'error': 'Failed to create callback', 'msg': str(e)}
    cal_json = cal.to_json()
    status = {'status': 'success'}
    return {**status, **cal_json}


@apfell.route(apfell.config['API_BASE'] + "/callbacks/<id:int>", methods=['GET'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user'], False)  # user or user-level api token are ok
async def get_one_callback(request, id, user):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    try:
        query = await db_model.callback_query()
        cal = await db_objects.get(query, id=id)
        return json(cal.to_json())
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'failed to get callback'}, 404)


@apfell.route(apfell.config['API_BASE'] + "/callbacks/<id:int>/loaded_commands", methods=['GET'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user'], False)  # user or user-level api token are ok
async def get_loaded_commands_for_callback(request, id, user):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    try:
        query = await db_model.operation_query()
        operation = await db_objects.get(query, name=user['current_operation'])
        query = await db_model.callback_query()
        callback = await db_objects.get(query, id=id, operation=operation)
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'Failed to get the current operation or that callback'})
    query = await db_model.loadedcommands_query()
    loaded_commands = await db_objects.execute(query.where(LoadedCommands.callback == callback))
    return json({'status': 'success', 'loaded_commands': [{'command': lc.command.cmd,
                                                           'version': lc.version,
                                                           'apfell_version': lc.command.version} for lc in loaded_commands]})


@apfell.route(apfell.config['API_BASE'] + "/callbacks/<id:int>", methods=['PUT'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user', 'auth:apitoken_c2'], False)
async def update_callback(request, id, user):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    data = request.json
    try:
        query = await db_model.operation_query()
        operation = await db_objects.get(query, name=user['current_operation'])
        query = await db_model.callback_query()
        cal = await db_objects.get(query, id=id, operation=operation)
        if 'description' in data:
            if data['description'] == 'reset':
                # set the description back to what it was from the payload
                cal.description = cal.registered_payload.tag
            else:
                cal.description = data['description']
        if 'active' in data:
            if data['active'] == 'true':
                cal.active = True
            elif data['active'] == 'false':
                cal.active = False
        if 'encryption_type' in data:
            cal.encryption_type = data['encryption_type']
        if 'encryption_key' in data:
            cal.encryption_key = data['encryption_key']
        if 'decryption_key' in data:
            cal.decryption_key = data['decryption_key']
        if 'parent' in data:
            try:
                if data['parent'] == -1:
                    # this means to remove the current parent
                    cal.pcallback = None
                else:
                    query = await db_model.callback_query()
                    parent = await db_objects.get(query, id=data['parent'], operation=operation)
                    if parent.id == cal.id:
                        return json({'status': 'error', 'error': 'cannot set parent = child'})
                    cal.pcallback = parent
            except Exception as e:
                return json({'status': 'error', 'error': "failed to set parent callback: " + str(e)})
        await db_objects.update(cal)
        success = {'status': 'success'}
        updated_cal = cal.to_json()
        return json({**success, **updated_cal})
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'failed to update callback: ' + str(e)})


@apfell.route(apfell.config['API_BASE'] + "/callbacks/<id:int>", methods=['DELETE'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user'], False)  # user or user-level api token are ok
async def remove_callback(request, id, user):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    try:
        query = await db_model.callback_query()
        cal = await db_objects.get(query, id=id)
        if user['admin'] or cal.operation.name in user['operations']:
            cal.active = False
            await db_objects.update(cal)
            success = {'status': 'success'}
            deleted_cal = cal.to_json()
            return json({**success, **deleted_cal})
        else:
            return json({'status': 'error', 'error': 'must be an admin or part of that operation to mark it as no longer active'})
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': "failed to delete callback: " + str(e)})


@apfell.route(apfell.config['API_BASE'] + "/callbacks/<id:int>/all_tasking", methods=['GET'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user'], False)  # user or user-level api token are ok
async def callbacks_get_all_tasking(request, user, id):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    # Get all of the tasks and responses so far for the specified agent
    try:
        query = await db_model.operation_query()
        operation = await db_objects.get(query, name=user['current_operation'])
        query = await db_model.callback_query()
        callback = await db_objects.get(query, id=id, operation=operation)
        cb_json = callback.to_json()
        cb_json['tasks'] = []
        query = await db_model.task_query()
        tasks = await db_objects.prefetch(query.where(Task.callback == callback).order_by(Task.id), Command.select())
        for t in tasks:
            query = await db_model.response_query()
            responses = await db_objects.execute(query.where(Response.task == t).order_by(Response.id))
            cb_json['tasks'].append({**t.to_json(), "responses": [r.to_json() for r in responses]})
        return json({'status': 'success', **cb_json})
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': str(e)})


@apfell.route(apfell.config['API_BASE'] + "/callbacks/<id:int>/keys", methods=['GET'])
@inject_user()
@scoped(['auth:user', 'auth:apitoken_user', 'auth:apitoken_c2'], False)
async def get_callback_keys(request, user, id):
    if user['auth'] not in ['access_token', 'apitoken']:
        abort(status_code=403, message="Cannot access via Cookies. Use CLI or access via JS in browser")
    try:
        query = await db_model.operation_query()
        operation = await db_objects.get(query, name=user['current_operation'])
        query = await db_model.callback_query()
        callback = await db_objects.get(query, id=id, operation=operation)
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'failed to find callback'})
    encryption_type = callback.encryption_type if callback.encryption_type else ""
    decryption_key = callback.decryption_key if callback.decryption_key else ""
    encryption_key = callback.encryption_key if callback.encryption_key else ""
    return json({'status': 'success', 'encryption_type': encryption_type, 'decryption_key': decryption_key,
                 'encryption_key': encryption_key})