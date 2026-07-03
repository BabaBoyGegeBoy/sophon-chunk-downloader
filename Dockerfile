FROM python:3.11-slim

WORKDIR /app

# 安装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目源码
COPY . .

# 数据目录（需挂载 pkg_version 仓库与输出目录）
VOLUME ["/app/pkg_version", "/app/output"]

ENTRYPOINT ["python", "main.py"]
