#!/bin/zsh

cd instance_pipeline/code/
rm code.zip
echo "Deleted instance_pipeline"
zip code.zip main.py
echo "Created new code.zip for instance_pipeline"
echo ""
echo ""
cd ../../ssm_accessibility/code
rm code.zip
echo "Deleted ssm_accessibility"
zip code.zip main.py
echo "Created new code.zip for ssm_accessibility"
echo ""
echo ""
cd ../../sensor_installer/code
rm code.zip
echo "Deleted sensor_installer"
zip code.zip main.py
echo "Created new code.zip for sensor_installer"