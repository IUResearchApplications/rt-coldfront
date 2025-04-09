python ./manage.py migrate
python ./manage.py collectstatic --noinput

if [ "$BUILD_ENV" == "local" ]; then
    python ./manage.py runserver_plus 0.0.0.0:8000 --cert-file /opt/coldfront/cert.pem  --key-file /opt/coldfront/key.pem
else
    gunicorn --workers 3 --bind unix:coldfront.sock -m 007 coldfront.config.wsgi
fi