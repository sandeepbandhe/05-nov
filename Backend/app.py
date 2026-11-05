import os
from flask import Flask, jsonify, request
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions
from supabase_auth.errors import AuthApiError
from postgrest import APIError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)


def get_authenticated_client():
    scheme, separator, access_token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not access_token:
        return None, None, (jsonify(error="Authentication required"), 401)

    try:
        auth_response = supabase.auth.get_user(access_token)
    except AuthApiError:
        return None, None, (jsonify(error="Invalid or expired access token"), 401)
    except Exception:
        return None, None, (jsonify(error="Authentication service unavailable"), 503)

    if not auth_response or not auth_response.user:
        return None, None, (jsonify(error="Invalid or expired access token"), 401)

    user_client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
        SyncClientOptions(
            headers={"Authorization": f"Bearer {access_token}"},
            auto_refresh_token=False,
            persist_session=False,
        ),
    )
    return user_client, auth_response.user.id, None


@app.route('/')
def index():
    try:
        response = supabase.table('users').select("*").execute()
    except APIError as error:
        return f'<p>Error loading users: {error.message}</p>'

    users = response.data

    html = '<h1>users</h1><ul>'
    for instrument in users:
        html += f'<li>{instrument["email"]}</li>'
    html += '</ul>'

    return html


@app.get('/api/events')
def list_events():
    limit = min(max(request.args.get('limit', default=50, type=int) or 50, 1), 100)
    offset = max(request.args.get('offset', default=0, type=int) or 0, 0)
    try:
        response = supabase.table('events').select('*').range(offset, offset + limit - 1).execute()
    except APIError:
        return jsonify(error="Unable to load events"), 500

    return jsonify(events=response.data)


@app.get('/api/events/<event_id>')
def get_event(event_id):
    try:
        response = supabase.table('events').select('*').eq('id', event_id).maybe_single().execute()
    except APIError:
        return jsonify(error="Unable to load event"), 500

    if not response.data:
        return jsonify(error="Event not found"), 404
    return jsonify(event=response.data)


@app.post('/api/registrations')
def create_registration():
    client, user_id, error_response = get_authenticated_client()
    if error_response:
        return error_response

    body = request.get_json(silent=True)
    event_id = body.get('event_id') if isinstance(body, dict) else None
    if isinstance(event_id, bool) or not isinstance(event_id, (str, int)) or not str(event_id).strip():
        return jsonify(error="event_id is required"), 400

    try:
        existing = (
            client.table('registrations')
            .select('event_id')
            .eq('event_id', event_id)
            .eq('user_id', user_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            return jsonify(error="Already registered for this event"), 409

        response = client.table('registrations').insert({
            'event_id': event_id,
            'user_id': user_id,
        }).execute()
    except APIError as error:
        if error.code == '23505':
            return jsonify(error="Already registered for this event"), 409
        return jsonify(error="Unable to create registration"), 500

    registration = response.data[0] if response.data else None
    return jsonify(registration=registration), 201


@app.get('/api/me/registrations')
def list_my_registrations():
    client, user_id, error_response = get_authenticated_client()
    if error_response:
        return error_response

    try:
        response = client.table('registrations').select('*').eq('user_id', user_id).execute()
    except APIError:
        return jsonify(error="Unable to load registrations"), 500

    return jsonify(registrations=response.data)


@app.get('/api/events/<event_id>/registrations')
def list_event_registrations(event_id):
    client, user_id, error_response = get_authenticated_client()
    if error_response:
        return error_response

    try:
        event_response = client.table('events').select('id, organizer_id').eq('id', event_id).maybe_single().execute()
        if not event_response.data:
            return jsonify(error="Event not found"), 404
        if event_response.data.get('organizer_id') != user_id:
            return jsonify(error="Only the event organizer can view registrations"), 403

        response = client.table('registrations').select('*').eq('event_id', event_id).execute()
    except APIError:
        return jsonify(error="Unable to load event registrations"), 500

    return jsonify(registrations=response.data)

if __name__ == '__main__':
    app.run(debug=True)
