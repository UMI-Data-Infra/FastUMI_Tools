#include <xv-sdk.h>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>

static std::string json_escape(const std::string& value)
{
  std::ostringstream output;
  for (unsigned char c : value)
  {
    switch (c)
    {
      case '"': output << "\\\""; break;
      case '\\': output << "\\\\"; break;
      case '\b': output << "\\b"; break;
      case '\f': output << "\\f"; break;
      case '\n': output << "\\n"; break;
      case '\r': output << "\\r"; break;
      case '\t': output << "\\t"; break;
      default:
        if (c < 0x20)
        {
          const char* hex = "0123456789abcdef";
          output << "\\u00" << hex[(c >> 4) & 0xf] << hex[c & 0xf];
        }
        else
        {
          output << static_cast<char>(c);
        }
    }
  }
  return output.str();
}

int main(int argc, char** argv)
{
  std::ostringstream sdk_version;
  sdk_version << xv::version();

  const std::string mode = argc > 1 ? argv[1] : "--version";
  if (mode == "--version")
  {
    std::cout << sdk_version.str() << std::endl;
    return 0;
  }
  if (mode != "--devices")
  {
    std::cerr << "usage: xvsdk_version [--version|--devices]" << std::endl;
    return 2;
  }

  try
  {
    const auto devices = xv::getDevices(3.0);
    std::cout << "XVISION_PROBE_JSON_BEGIN\n";
    std::cout << "{\"sdk_version\":\"" << json_escape(sdk_version.str()) << "\",\"devices\":[";
    bool first_device = true;
    for (const auto& pair : devices)
    {
      if (!first_device) std::cout << ",";
      first_device = false;
      std::cout << "{\"serial\":\"" << json_escape(pair.first) << "\",\"info\":{";
      bool first_info = true;
      const auto info = pair.second->info();
      for (const auto& item : info)
      {
        if (!first_info) std::cout << ",";
        first_info = false;
        std::cout << "\"" << json_escape(item.first) << "\":\"" << json_escape(item.second) << "\"";
      }
      std::cout << "}}";
    }
    std::cout << "]}\nXVISION_PROBE_JSON_END\n";
    return 0;
  }
  catch (const std::exception& error)
  {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
