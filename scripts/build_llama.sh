#!/data/data/com.termux/files/usr/bin/sh
# Fallback on-device compiler for llama.cpp (used on Android < 9 or if prebuilt binaries fail).
# Usage: build_llama.sh [armv8-a | armv8.2-a+dotprod]
set -e

export PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export HOME="${HOME:-/data/data/com.termux/files/home}"
export PATH="$PREFIX/bin:$PATH"
export TMPDIR="${TMPDIR:-$PREFIX/tmp}"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-$PREFIX/lib}"
export DEBIAN_FRONTEND=noninteractive

ARM_ARCH="${1:-armv8-a}"
APT_OPTS="-o Dpkg::Options::=--force-confnew -o Dpkg::Options::=--force-confdef"

echo "[*] Installing build toolchain inside Termux (clang, cmake, git, make)..."
yes | pkg update -y $APT_OPTS
yes | pkg upgrade -y $APT_OPTS
yes | pkg install -y $APT_OPTS clang cmake git make

cd "$HOME"
if [ ! -d "$HOME/llama.cpp/.git" ]; then
  rm -rf "$HOME/llama.cpp"
  echo "[*] Cloning llama.cpp (shallow)..."
  git clone --depth 1 https://github.com/ggml-org/llama.cpp
fi

cd "$HOME/llama.cpp"
echo "[*] Configuring CMake for pure CPU ($ARM_ARCH)..."
cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DGGML_CPU_ARM_ARCH="$ARM_ARCH" \
  -DCMAKE_C_FLAGS="-march=$ARM_ARCH -O3" \
  -DCMAKE_CXX_FLAGS="-march=$ARM_ARCH -O3" \
  -DGGML_VULKAN=OFF \
  -DGGML_CUDA=OFF \
  -DGGML_OPENCL=OFF \
  -DGGML_METAL=OFF \
  -DLLAMA_CURL=OFF \
  -DLLAMA_OPENSSL=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF

echo "[*] Building llama-server, llama-cli, llama-bench (-j4 thermal cap)..."
cmake --build build --config Release -j4 --target llama-server llama-cli llama-bench

mkdir -p "$HOME/llama"
cp -af build/bin/* "$HOME/llama/"
echo "[+] Source build complete!"
