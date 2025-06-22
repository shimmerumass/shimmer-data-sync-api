rm -rf lambda_package
mkdir lambda_package
pip install --target=lambda_package fastapi mangum boto3 python-dotenv
cp main.py lambda_package/
cd lambda_package
zip -r ../lambda-deploy.zip .
cd ..