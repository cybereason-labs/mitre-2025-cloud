#!/bin/zsh

dirs=("instance_pipeline" "ssm_accessibility" "sensor_installer")

for d in "${dirs[@]}"; do
    zip_file="../$d/code/code.zip"
    py_file="../$d/code/main.py"

    echo "$d"
    echo "================="
    if [ -f "$zip_file" ]; then
        echo "INFO :: Removing $zip_file"
        rm -f "$zip_file"
    fi

    if [ -f "$py_file" ]; then
        echo "INFO :: Creating $zip_file with $py_file"
        zip "$zip_file" "$py_file"
    else
        echo "WARNING :: $py_file not found, skipping..."
    fi
    echo ""
done
