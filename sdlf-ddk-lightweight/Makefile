SHELL=/bin/bash
CICD=default
CHILD=default
REGION=$(shell aws configure get region --profile ${CICD})
ENV=dev


.PHONY: empty_buckets delete_sdlf delete_bootstrap delete_repositories delete_all_items


delete_all: empty_buckets delete_sdlf delete_bootstrap delete_repositories delete_all_items

help:
	@echo "Helper for the aws-sdlf-ddk MakeFile";
	@echo "For clean up of the solution execute the following command";
	@echo "make delete_all CICD=\"<your_cicd_profile>\" REGION=\"<the_deployment_region>\"";
	@echo "-------------------------------------------\n";



delete_repositories:
	./scripts/cleanup_scripts/delete_repositories.sh -s ${CICD} -t ${CHILD} -r ${REGION} -d sdlf-ddk-example

empty_buckets:	
	pushd scripts/cleanup_scripts; python3 ./list_items_to_delete.py ${ENV} ${CHILD}; popd;
	pushd scripts/cleanup_scripts; python3 ./empty_buckets.py ${CHILD}; popd;
	
delete_sdlf:
	cdk destroy SDLF-DDK-${ENV}/sdlf-data-lake-pipeline --force --profile ${CHILD};

	cdk destroy sdlf-ddk-pipeline --force --profile ${CICD}
	
delete_bootstrap:
	aws cloudformation delete-stack --stack-name DdkDevBootstrap --profile ${CICD}

delete_all_items:
	sleep 120

	pushd scripts/cleanup_scripts; python3 ./list_items_to_delete.py ${ENV} ${CHILD}; popd;
	pushd scripts/cleanup_scripts; python3 ./delete_script.py ${CHILD}; popd;



