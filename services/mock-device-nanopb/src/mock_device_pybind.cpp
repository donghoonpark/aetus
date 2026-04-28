#include <pybind11/pybind11.h>

#include <stdexcept>
#include <string>
#include <vector>

extern "C" {
#include "mock_device_core.h"
}

namespace py = pybind11;

namespace {

py::bytes encode_telemetry(const std::string &device_id, const std::string &boot_id, std::uint64_t sequence) {
    std::vector<std::uint8_t> buffer(512);
    std::size_t encoded_size = 0;
    if (!encode_telemetry_event(buffer.data(), buffer.size(), &encoded_size, device_id.c_str(), boot_id.c_str(), sequence)) {
        throw std::runtime_error("nanopb telemetry encoding failed");
    }

    return py::bytes(reinterpret_cast<const char *>(buffer.data()), encoded_size);
}

py::bytes encode_status(
    const std::string &device_id,
    const std::string &boot_id,
    std::uint64_t sequence,
    const std::string &reboot_reason
) {
    std::vector<std::uint8_t> buffer(512);
    std::size_t encoded_size = 0;
    if (!encode_status_event(
            buffer.data(),
            buffer.size(),
            &encoded_size,
            device_id.c_str(),
            boot_id.c_str(),
            sequence,
            reboot_reason.c_str()
        )) {
        throw std::runtime_error("nanopb status encoding failed");
    }

    return py::bytes(reinterpret_cast<const char *>(buffer.data()), encoded_size);
}

}  // namespace

PYBIND11_MODULE(mock_device_py, m) {
    m.doc() = "Nanopb-based mock device encoder";
    m.def("encode_telemetry", &encode_telemetry, py::arg("device_id"), py::arg("boot_id"), py::arg("sequence"));
    m.def(
        "encode_status",
        &encode_status,
        py::arg("device_id"),
        py::arg("boot_id"),
        py::arg("sequence"),
        py::arg("reboot_reason") = "power_on"
    );
}
